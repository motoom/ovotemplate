#!/usr/bin/env python3

# TODO: String-only mode
# TODO: in template ook members kunnen accessen: {=cursus.lesvorm}, scheelt weer.

import os
import pprint
import re
import html
import unittest
import functools
import datetime

CHOPNAME = 1
CHOPITEM = 2

whitechars = re.compile(r"\s")
# A ranged repetition looks like "{#lijst:3:7 ...}"; 'x' means "no bound on this side".
rangerep = re.compile(r"(\w+):(-?\d+|x):(-?\d+|x)$")

def envflag(name, default=False):
    """Read a boolean setting from the environment, so a deployment can flip it
    without touching code. Accepts 1/true/yes/on (case-insensitive) as true."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


verbose = False
exceptionless = True  # False: throw exceptions when something is wrong with the template or rendering it; True: insert an error in the output text instead.
# True: HTML-escape substituted values; use {=!name} or a Raw() value to insert markup verbatim.
# OVOTEMPLATE_AUTOESCAPE=0 falls back to the old unescaped behaviour, so a deployment that
# trips over a template can be rolled back without a code change.
autoescape = envflag("OVOTEMPLATE_AUTOESCAPE", default=True)

# False: an unknown variable renders as nothing (like Jinja2's Undefined); True: it is
# reported (like Jinja2's StrictUndefined), which catches typos in variable names.
# Turn it on per deployment with OVOTEMPLATE_STRICT=1, or from your own settings with
# "ovotemplate.strictvars = settings.DEBUG" - assigning to it always wins over the environment.
strictvars = envflag("OVOTEMPLATE_STRICT")

MISSING = object()  # Sentinel: distinguishes "no default given" from a default of None.


def indent(level):
    return "| " + "    " * level


class Token(str):
    """A piece of template source that remembers where it started.
    Subclasses str, so the lexer, parser and compiler can keep treating tokens
    as plain strings while the position travels along for free."""

    def __new__(cls, text, line=0, column=0):
        self = super(Token, cls).__new__(cls, text)
        self.line = line
        self.column = column
        return self


class Node(list):
    """A parsed construct, remembering where its opening brace was, so an error
    in it can point at the right spot in the template."""

    def __init__(self, line=0, column=0):
        self.line = line
        self.column = column


def advance(line, column, text):
    "Return the (line, column) reached after reading text, starting from line/column."
    for c in text:
        if c == "\n":
            line += 1
            column = 1
        else:
            column += 1
    return line, column


def sourcelocation(name, line, column):
    """Describe a spot in a template for an error message, e.g. "offerte.tpl, line 42, column 17".
    Whatever isn't known (an unnamed template, an unpositioned node) is simply left out."""
    parts = []
    if name:
        parts.append(str(name))
    if line:
        parts.append("line %d" % line)
        parts.append("column %d" % column)
    return ", ".join(parts)


def errormessage(msg, kind=None, where=""):
    "Assemble a template error, naming the construct and the source position when known."
    prefix = "Template error"
    if kind:
        prefix += " in %s" % kind
    if where:
        prefix += " at %s" % where
    return "%s: %s" % (prefix, msg)


class Raw(str):
    """A string that is already markup and must never be escaped again.
    Setters produce these, so that {:x <b>hi</b>}{=x} keeps working under autoescape."""


class UnbalancedBrace(Exception):
    """A template has an opening brace without a matching closing one, or the other way round.
    Carries the position of the offending brace; process() fills in the template name."""

    def __init__(self, brace, line=0, column=0, templatename=None):
        self.brace = brace
        self.line = line
        self.column = column
        self.templatename = templatename
        super(UnbalancedBrace, self).__init__(brace)

    def __str__(self):
        where = sourcelocation(self.templatename, self.line, self.column)
        if where:
            return "unbalanced '%s' at %s" % (self.brace, where)
        return "unbalanced '%s'" % self.brace


def escape(value):
    "HTML-escape a substituted value, unless it is explicitly marked as Raw."
    if isinstance(value, Raw):
        return value
    return html.escape(value, quote=True)


def errorspan(msg):
    "Render a template error as a conspicuous inline span."
    return '<span class="ovotemplate_error" style="background-color: red; color: white;">%s</span>' % msg


def undefined(kind, name, where=""):
    """Decide what an unknown variable renders as.
    Lenient by default, like Jinja2's Undefined: a missing name simply produces nothing,
    which keeps optional fields out of the template. Set strictvars=True for Jinja2's
    StrictUndefined behaviour, where a typo in a variable name surfaces - with the spot
    in the template where it was written - instead of silently blanking part of the page."""
    if not strictvars:
        return ""
    msg = errormessage('unknown variable "%s"' % name, kind, where)
    if not exceptionless:
        raise KeyError(msg)
    return errorspan(msg)


def lookup(vars, name, default=MISSING):
    """Fetch 'name' from a render context, which may be a mapping (dict, Bunch)
    or an object with attributes (namedtuple, model instance, ...).
    Computed properties on the context's class are found too, so that presentation
    logic can live in a model (Cursus.duration) instead of in the template.
    Returns 'default' if absent; raises KeyError if absent and no default was given."""
    try:
        return vars[name]
    except KeyError:
        # A mapping, but without this key. A property on the class is still worth
        # trying; plain methods (dict.items, Bunch.get) deliberately are not, so that
        # a typo in a variable name keeps producing an error instead of a bound method.
        if isinstance(getattr(type(vars), name, None), property):
            return getattr(vars, name)
    except TypeError:
        # Not subscriptable by name (namedtuple, plain object, ...), so try attribute access.
        try:
            return getattr(vars, name)
        except AttributeError:
            pass
    if default is MISSING:
        raise KeyError(name)
    return default


class Container(list):
    "Generic container."

    # Where in which template this container was written. compile() fills these in;
    # containers built by hand simply keep the defaults and report no position.
    templatename = None
    line = 0
    column = 0

    def __init__(self, name="", usebraces=True):
        self.name = name
        self.usebraces = usebraces

    def where(self):
        "Where in the template source this container starts, for error messages."
        return sourcelocation(self.templatename, self.line, self.column)

    def setorigin(self, templatename, line, column):
        "Record where this container was written; returns self so it can be chained."
        self.templatename = templatename
        self.line = line
        self.column = column
        return self

    def __repr__(self):
        tag = "%s %s" % (self.__class__.__name__, self.name)
        where = self.where()
        if where:
            tag = "%s @ %s" % (tag.strip(), where)
        return "%s: %s" % (tag.strip(), super(Container, self).__repr__())

    def renderchildren(self, vars, last, level):
        """Render every child in order and concatenate the results.
        Every container type renders its contents through this method, so that
        value coercion (dates, numbers) behaves identically at every nesting level."""
        output = ""
        for child in self:
            if verbose:
                print("%s%s.render child %s" % (indent(level), self.__class__.__name__, child))
            value = child.render(vars, last, level + 1)
            if isinstance(value, datetime.datetime):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            if value:
                output += value
        return output

    def render(self, vars, last=False, level=0):
        if verbose:
            print("%sContainer.render(vars=%s,last=%s) type(vars)=%s, self.name=%s" % (indent(level), vars, last, type(vars), self.name))
        return self.renderchildren(vars, last, level)


class Lit(Container):
    "Container for literal content."

    def __init__(self, contents="", usebraces=True):
        super(Lit, self).__init__(usebraces=usebraces)
        self.append(contents)

    def __repr__(self):
        return super(Lit, self).__repr__()

    def render(self, vars, last=None, level=0):
        if verbose:
            print("%sLit.render(vars=%s,last=%s) type(vars)=%s, self=%s" % (indent(level), vars, last, type(vars), self))
        return self[0]


class Setter(Container):
    "Assigns the enclosed text to a var"

    def __init__(self, name, usebraces=True):
        super(Setter, self).__init__(name, usebraces=usebraces)
        self.name = name

    def __repr__(self):
        return super(Setter, self).__repr__()

    def render(self, vars, last, level):
        # Raw(): the captured text is markup the template itself produced, so reading it
        # back with {=name} must not escape it a second time.
        vars[self.name] = Raw(self.renderchildren(vars, last, level))


class External(Container):
    "External Content (file or verb)"

    def __init__(self, type, usebraces=True):
        super(External, self).__init__(type, usebraces=usebraces)
        self.type = type
        self.usebraces = usebraces

    def __repr__(self):
        return super(External, self).__repr__()

    def render(self, vars, last, level):
        if "file" in self.type:
            output = self.renderfile(vars, last, level, self.usebraces)
        elif "verb" in self.type:
            output = self.renderverb(vars, last, level)
        else:
            output = errorspan(errormessage('"%s" is an unknown external source-type' % self.type, "External", self.where()))
        return output

    def renderinner(self, vars, last, level):
        return self.renderchildren(vars, last, level)

    def renderverb(self, vars, last, level):
        filename = self.renderinner(vars, last, level)
        with open(filename, "r") as f:
            s = f.read()
        return s

    def renderfile(self, vars, last, level, usebraces):
        filename = self.renderinner(vars, last, level)
        tpl = Ovotemplate(usebraces=usebraces).fromfile(filename)
        output = tpl.render(vars)
        return output


class Sep(Container):
    "Container for separator. Same as Lit, but doesn't result in output in the last iteration of a Rep."

    def __init__(self, name, usebraces=True):
        super(Sep, self).__init__(name, usebraces=usebraces)

    def __repr__(self):
        return super(Sep, self).__repr__()

    def render(self, vars, last, level):
        if verbose:
            print("%sSep.render(vars=%s) type(vars)=%s, self=%s" % (indent(level), vars, type(vars), self))
        if last:
            if verbose:
                print("%sSep.render last is True, empty string returned" % indent(level))
            return ""
        return self.renderchildren(vars, last, level)


class Sub(Container):
    "Container for a variable substitution."

    def __init__(self, name, usebraces=True):
        self.raw = name.startswith("!")  # {=!name} inserts the value without HTML-escaping.
        if self.raw:
            name = name[1:]
        super(Sub, self).__init__(name, usebraces=usebraces)

    def __repr__(self):
        return super(Sub, self).__repr__()

    def render(self, vars, last, level):
        if verbose:
            print("%sSub.render(vars=%s) type(vars)=%s, self.name=%s, self.raw=%s" % (indent(level), vars, type(vars), self.name, self.raw))
        try:
            value = lookup(vars, self.name)
        except KeyError:
            return undefined("Sub", self.name, self.where())
        if isinstance(value, (int, float)):
            value = str(value)
        if autoescape and not self.raw and isinstance(value, str):
            value = escape(value)
        return value


class Cond(Container):
    "Container for conditional content."

    def __init__(self, name, inverting=False, usebraces=True):
        super(Cond, self).__init__(name, usebraces=usebraces)
        self.inverting = inverting

    def __repr__(self):
        return super(Cond, self).__repr__()

    def render(self, vars, last, level):
        if verbose:
            print("%sCond.render(vars=%s) type(vars)=%s, self.name=%s, self.inverting=%s" % (indent(level), vars, type(vars), self.name, self.inverting))
        # A missing variable counts as False, in both modes: "{!name ...}" is the
        # established idiom for "if this isn't set", and unlike Jinja2 there is no
        # separate "is defined" test to fall back on.
        ok = lookup(vars, self.name, None)
        if self.inverting:
            ok = not ok
        if not ok:
            if verbose:
                print("%sCond.render cond is False, empty string returned" % indent(level))
            return ""
        return self.renderchildren(vars, last, level)


class Counter(Container):
    "Container for counted content."

    def __init__(self, name, count=0, compare=0, usebraces=True):
        super(Counter, self).__init__(name, usebraces=usebraces)
        self.name = name
        self.count = count
        self.compare = compare

    def __repr__(self):
        return super(Counter, self).__repr__()

    def render(self, vars, last, level):
        if verbose:
            print("%sCounter.render(vars=%s) type(vars)=%s, self.name=%s, self.count=%d" % (indent(level), vars, type(vars), self.name, self.count))
        item = lookup(vars, self.name, None)
        output = ""

        if item is not None:
            if self.compare == 0:
                ok = len(item) == self.count
            elif self.compare < 0:
                ok = len(item) < self.count
            elif self.compare > 0:
                ok = len(item) > self.count

            if ok:
                output = self.renderchildren(vars, last, level)
            elif verbose:
                print("%sCount.render len(%s) doesn't %d %d, empty string returned" % (indent(level), self.name, self.compare, self.count))

        return output


class Rep(Container):
    "Container for repeating content."

    def __init__(self, name, usebraces=True):
        self.verbose = False

        rangedefinition = rangerep.match(name)

        if rangedefinition:
            if verbose or self.verbose:
                print("Ranged repetition for...")

            name = rangedefinition.group(1)
            self.start = None if rangedefinition.group(2) == 'x' else int(rangedefinition.group(2))
            self.end = None if rangedefinition.group(3) == 'x' else int(rangedefinition.group(3))

            if verbose or self.verbose:
                print("...%s between from item %s up to and not including %s" % (name, self.start, self.end))
        else:
            self.start = self.end = None

            if verbose or self.verbose:
                print("Reptition %s isn't ranged" % name)

        super(Rep, self).__init__(name, usebraces=usebraces)

    def __repr__(self):
        return super(Rep, self).__repr__()

    def render(self, vars, last, level):
        if verbose or self.verbose:
            print("%sRep.render(vars=%s) type(vars)=%s, self.name=%s" % (indent(level), vars, type(vars), self.name))
        output = ""
        # TODO: Dit kan nuttige debug info opleveren: if not self.name in vars: raise NameNotFound("A required variable name '%s' was not present in '%r'" % (self.name, vars))
        try:
            subvars = lookup(vars, self.name)  # A KeyError here means that a required variable wasn't present.
        except KeyError:
            return undefined("Rep", self.name, self.where())

        # Resolve the range to plain indices, following Python's own slice semantics:
        # a negative bound counts back from the end, and both are clamped to the list.
        count = len(subvars)
        start = 0 if self.start is None else self.start
        end = count if self.end is None else self.end
        if start < 0:
            start = count + start
        if end < 0:
            end = count + end
        start = min(max(start, 0), count)
        end = min(max(end, start), count)

        for nr, subvar in enumerate(subvars):
            if start <= nr < end:
                islast = nr == end - 1  # Last of the *rendered* range, so {/sep} stops there.
                if verbose or self.verbose:
                    print("%sRep.render subvar=%s, type(subvar)=%s, last=%s" % (indent(level), subvar, type(subvar), islast))
                output += self.renderchildren(subvar, islast, level)
        return output


def splitfirst(s):
    "Split a string into a first special word, and the rest."
    if not s:
        return "", ""
    if s[0] in createinfo:
        parts = whitechars.split(s, 1)
        if len(parts) < 2:
            return s, ""
        else:
            return tuple(parts)
    else:
        return "", s


def feed(seq):
    for item in seq:
        yield item


def lexer(it, openbrace, closebrace):
    """Split input into tokens. A token is either an open brace, a closing brace, or a
    string without braces. Each token records the line and column where it started,
    counting from 1, so errors can point back into the template source."""
    tokens = []
    token = ""
    line = column = 1
    startline = startcolumn = 1
    for c in it:
        if c == openbrace or c == closebrace:
            if token:
                tokens.append(Token(token, startline, startcolumn))
                token = ""
            tokens.append(Token(c, line, column))
        else:
            if not token:
                startline, startcolumn = line, column
            token += c
        if c == "\n":
            line += 1
            column = 1
        else:
            column += 1
    if token:
        tokens.append(Token(token, startline, startcolumn))
    return tokens


def parse(it, node, openbrace, closebrace, nesting=0):
    """Build a (recursive) nested list from the tokens, carrying each construct's
    opening-brace position along into the Node that represents it."""
    for token in it:
        if token == openbrace:
            subnode = Node(getattr(token, "line", 0), getattr(token, "column", 0))
            node.append(subnode)
            parse(it, subnode, openbrace, closebrace, nesting + 1)
        elif token == closebrace:
            if nesting == 0:
                raise UnbalancedBrace(closebrace, getattr(token, "line", 0), getattr(token, "column", 0))
            return
        else:
            node.append(token)
    if nesting:
        # Ran out of tokens while still inside a construct; point at its opening brace.
        raise UnbalancedBrace(openbrace, getattr(node, "line", 0), getattr(node, "column", 0))


createinfo = {
    "?": (Cond, CHOPNAME),
    "!": (functools.partial(Cond, inverting=True), CHOPNAME),
    "#": (Rep, CHOPNAME),
    "=": (Sub, CHOPITEM),
    "/": (Sep, CHOPNAME),
    "$": (External, CHOPNAME),
    ":": (Setter, CHOPNAME),
    "|": (functools.partial(Counter, count=1, compare=0), CHOPNAME),
    "+": (functools.partial(Counter, count=1, compare=1), CHOPNAME),
    }


def compile(node, into, usebraces, openbrace, closebrace, templatename=None, level=0):
    if verbose:
        print("%s compile: " % indent(level), node)
    for pos, item in enumerate(node):
        if isinstance(item, list):
            if verbose:
                print("%s #%d list: %r" % (indent(level), pos, item))
            # The construct's opening brace is the spot to blame for anything inside it.
            line, column = getattr(item, "line", 0), getattr(item, "column", 0)
            where = sourcelocation(templatename, line, column)
            head = item[0] if item else ""
            if not head or not head[0] in createinfo:
                msg = errormessage("'%s' without a following valid metachar" % openbrace, where=where)
                if exceptionless:
                    into.append(Lit(errorspan(msg)).setorigin(templatename, line, column))
                    continue  # Report this one construct, but keep compiling the rest of the template.
                else:
                    raise ValueError(msg)
            first, rest = splitfirst(head)
            operator, name = first[0], first[1:]
            if verbose:
                print("%s operator %s, name %s, rest %r" % (indent(level), operator, name, rest))
            # Create correct container
            factoryfunc, options = createinfo[operator]
            ob = factoryfunc(name, usebraces=usebraces).setorigin(templatename, line, column)
            if options == CHOPNAME:
                # What follows the metachar and name is literal text; it starts just past
                # the name and its separating whitespace, so walk the position along.
                restline, restcolumn = advance(getattr(head, "line", 0), getattr(head, "column", 0),
                                               head[:len(head) - len(rest)])
                item[0] = Token(rest, restline, restcolumn)
            elif options == CHOPITEM:
                item = item[1:]
            into.append(compile(item, ob, usebraces, openbrace, closebrace, templatename, level + 1))
        else:
            if verbose:
                print("%s #%d item: %s" % (indent(level), pos, item))
            into.append(Lit(item).setorigin(templatename, getattr(item, "line", 0), getattr(item, "column", 0)))
    return into


def process(sourcetext, usebraces, openbrace, closebrace, name=None):
    if verbose:
        print("\n\n\nCompile phase")
    tokens = lexer(feed(sourcetext), openbrace, closebrace)
    root = Node()
    try:
        parse(feed(tokens), root, openbrace, closebrace)
    except UnbalancedBrace as e:
        e.templatename = name  # Only process() knows which template this was.
        if not exceptionless:
            raise
        result = Container(usebraces=usebraces)
        result.append(Lit(errorspan(errormessage(str(e)))))
        return result
    result = compile(root, Container(usebraces=usebraces), usebraces, openbrace, closebrace, name)
    if verbose:
        print("Compile result:", result)
    return result


class Ovotemplate(object):
    """Simple templating class."""

    def __init__(self, s=None, name=None, usebraces=True):
        """Initialize a template, optionally from a template string."""
        self.usebraces = usebraces
        if self.usebraces:
            self.openbrace = "{"
            self.closebrace = "}"
        else:
            self.openbrace = "«"
            self.closebrace = "»"
        self.name = name  # Known before compiling, so errors can name the template.
        if s:
            self.root = process(s, self.usebraces, self.openbrace, self.closebrace, self.name)
        elif s is not None:
            self.root = Container(usebraces=self.usebraces)
            self.root.append(Lit(""))
        else:
            self.root = None

    def fromfile(self, fn):
        """Load a template from a file.
        Allows: tem = Ovotemplate().fromfile("hello.tpl")
        The template file should contain UTF-8 encoded unicode text
        """
        with open(fn) as f:
            tpl = f.read()
        self.name = fn.replace(" ", "_")
        self.root = process(tpl, self.usebraces, self.openbrace, self.closebrace, self.name)
        return self

    def pprint(self):
        """Pretty-print the template structure."""
        pprint.pprint(self.root)

    def render(self, vars):
        """Renders the template to a string, using the supplied variables."""
        if verbose:
            print("\nRender phase")
        if not self.root:
            raise Exception("You should either pass a template as a string in the constructor, or use 'fromfile' to read the template from file")
        result = self.root.render(vars)
        if verbose:
            print("Render result:", result)
        return result


class Test(unittest.TestCase):
    """Unittest for Ovotemplate."""

    def test_naming(self):
        """Test the naming; every template instance can have a name (usually the filename where it was loaded from).
        This name is used in error reporting."""
        tems = "{=name}"
        tem = Ovotemplate(tems, "nametest")
        self.assertEqual(tem.name, "nametest")

    def test_badmetachar(self):
        tems = "{&name}"  # Note that '&' is illegal after a '{'.
        #
        global exceptionless
        prevexceptionless = exceptionless
        #
        exceptionless = False
        self.assertRaises(ValueError, Ovotemplate, tems)
        #
        exceptionless = True
        tem = Ovotemplate(tems)
        res = tem.render({})
        self.assertTrue("Template error" in res)
        exceptionless = prevexceptionless

    def test_alternatebraces(self):
        tem = Ovotemplate("Hello, {=name}!", "nametest")
        self.assertEqual(tem.render(dict(name="world")), "Hello, world!")
        tem = Ovotemplate("Hello, «=name»!", "nametest", usebraces=False) # Note that unicode must be used here
        self.assertEqual(tem.render(dict(name="world")), "Hello, world!")

    def DISABLED_test_alternatebraces_extern(self):
        tem = Ovotemplate("«$verb templates/nl/unittest-helper-guillemets.tpl»", {}, usebraces=False)
        res = tem.render({"age": 42})
        self.assertEqual(res, "Voor gebruik in unittests van ovotemplate. Het Universum is «=age» jaar oud.\n")
        tem = Ovotemplate("«$file templates/nl/unittest-helper-guillemets.tpl»", {}, usebraces=False)
        res = tem.render({"age": 42})
        self.assertEqual(res, "Voor gebruik in unittests van ovotemplate. Het Universum is 42 jaar oud.\n")

    def test_splitting(self):
        self.assertEqual(splitfirst(""), ("", ""))
        self.assertEqual(splitfirst("?hi"), ("?hi", ""))
        self.assertEqual(splitfirst("?hi there"), ("?hi", "there"))
        self.assertEqual(splitfirst("hi"), ("", "hi"))
        self.assertEqual(splitfirst("hi there"), ("", "hi there"))

    def test_render(self):
        """Test a number of progressively complex render cases. (template source code, context variables, expected result text)."""
        goodcases = (
            # Empty template.
            ("", {}, ""),
            # Just a letter.
            ("a", {}, "a"),
            # Longer string.
            ("hi there", {}, "hi there"),
            # Simple substitution.
            ("{=status}", {"status": "STATUS"}, "STATUS"),
            ("{=status}", {"status": 67.2334}, "67.2334"),
            ("{=status}", {"status": None}, ""),
            ("{=status}", {"status": False}, "False"),
            ("BEFORE{=status}", {"status": "STATUS"}, "BEFORESTATUS"),
            ("{=status}AFTER", {"status": "STATUS"}, "STATUSAFTER"),
            # Two substitutions in different flavors.
            ("{=one}{=two}", {"one": "ONE", "two": "TWO"}, "ONETWO"),
            ("{=one}AND{=two}", {"one": "ONE", "two": "TWO"}, "ONEANDTWO"),
            ("{=one} {=two}", {"one": "ONE", "two": "TWO"}, "ONE TWO"),
            ("{=one}   {=two}", {"one": "ONE", "two": "TWO"}, "ONE   TWO"),
            ("{=one}, {=two}", {"one": "ONE", "two": "TWO"}, "ONE, TWO"),
            ("{=one} ({=two})", {"one": "ONE", "two": "TWO"}, "ONE (TWO)"),
            # Substitution with text in between.
            ("well{=here}it{=goes}with{=some}test",
                {"here": "HERE", "goes": "GOES", "some": "SOME"},
                "wellHEREitGOESwithSOMEtest"),

            ('{?useimg hallo <img src="path/names/{=component}/with/{=component}.jpg">}',
                {"useimg": True, "component": "filesystem"},
                'hallo <img src="path/names/filesystem/with/filesystem.jpg">'),

            # Simple repetitions.
            ("{#cls{=co}}",
                {"cls": ({"co": "red"}, {"co": "gr"}, {"co": "bl"})},
                "redgrbl"),
            ("{#cls <{=co}>}",
                {"cls": ({"co": "red"}, {"co": "gr"}, {"co": "bl"})},
                "<red><gr><bl>"),
            ("{#cls {=co}, }",
                {"cls": ({"co": "red"}, {"co": "gr"}, {"co": "bl"})},
                "red, gr, bl, "),
            ("{#cls {=co} x }",
                {"cls": ({"co": "red"}, {"co": "gr"}, {"co": "bl"})},
                "red x gr x bl x "),
            ("{#cls {=co} _}",
                {"cls": ({"co": "red"}, {"co": "gr"}, {"co": "bl"})},
                "red _gr _bl _"),
            # Simple conditions.
            ("throw a {?condition big }party",
                {"condition": True},
                "throw a big party"),
            ("throw a {?condition big }tantrum",
                {"condition": 42},
                "throw a big tantrum"),
            ("throw a {?condition big }party",
                {"condition": False},
                "throw a party"),
            ("throw a {?condition big }tantrum",
                {"condition": None},
                "throw a tantrum"),
            ("A!{?condition B}!C!{!condition D}!E",
                {"condition": True},
                "A!B!C!!E"),
            ("A!{?condition B}!C!{!condition D}!E",
                {"condition": False},
                "A!!C!D!E"),
            # Repeats.
            ("{#a{=b}{=c}}",
                {"a": ({"b": 11, "c": 22},)},
                "1122"),
            ("{#a {=b} {=c}}",
                {"a": [{"b": 33, "c": 44}]},
                "33 44"),
            ("{#a STA{=b}STO  BEG{=c}END }",
                {"a": ({"b": 55, "c": 66},)},
                "STA55STO  BEG66END "),
            ("{#a {=b} {=c}}",
                {"a": ({"b": 7.70, "c": 88}, {"b": 99, "c": 1.234567})},
                "7.7 8899 1.234567"),
            ("{#a {=b} {=c}}", {"a": ()}, ""),
            # Ranged repetitions.
            ("{#lijst:3:7 {=waarde}}",
                {
                    "lijst": (
                        {"waarde": "0"},
                        {"waarde": "1"},
                        {"waarde": "2"},
                        {"waarde": "3"},
                        {"waarde": "4"},
                        {"waarde": "5"},
                        {"waarde": "6"},
                        {"waarde": "7"},
                        {"waarde": "8"}
                        )
                    },
                "3456"),
            ("{#lijst:3:100 {=waarde}}",
                {
                    "lijst": (
                        {"waarde": "0"},
                        {"waarde": "1"},
                        {"waarde": "2"},
                        {"waarde": "3"},
                        {"waarde": "4"},
                        {"waarde": "5"},
                        {"waarde": "6"},
                        {"waarde": "7"},
                        {"waarde": "8"}
                        )
                    },
                "345678"),
            ("{#lijst:0:4 {=waarde}}",
                {
                    "lijst": (
                        {"waarde": "0"},
                        {"waarde": "1"},
                        {"waarde": "2"},
                        {"waarde": "3"},
                        {"waarde": "4"},
                        {"waarde": "5"},
                        {"waarde": "6"},
                        {"waarde": "7"},
                        {"waarde": "8"}
                        )
                    },
                "0123"),
            # Ranged repetitions with negative bounds, following Python slice semantics.
            ("{#l:-2:x {=v}}",
                {"l": ({"v": "a"}, {"v": "b"}, {"v": "c"}, {"v": "d"})},
                "cd"),
            ("{#l:x:-1 {=v}}",
                {"l": ({"v": "a"}, {"v": "b"}, {"v": "c"}, {"v": "d"})},
                "abc"),
            ("{#l:-3:-1 {=v}}",
                {"l": ({"v": "a"}, {"v": "b"}, {"v": "c"}, {"v": "d"})},
                "bc"),
            # Out-of-range bounds are clamped, not an error.
            ("{#l:-99:99 {=v}}",
                {"l": ({"v": "a"}, {"v": "b"})},
                "ab"),
            ("{#l:3:1 {=v}}",
                {"l": ({"v": "a"}, {"v": "b"})},
                ""),
            # A separator inside a ranged repetition stops at the end of the range,
            # not at the end of the underlying list.
            ("{#l:0:2 {=v}{/sep , }}",
                {"l": ({"v": "a"}, {"v": "b"}, {"v": "c"})},
                "a, b"),
            ("{#l:1:3 {=v}{/sep , }}",
                {"l": ({"v": "a"}, {"v": "b"}, {"v": "c"})},
                "b, c"),
            # Repeat with variabele as last on the line.
            ("{#blop\n{=you}}",
                dict(blop=(dict(you=123), dict(you=456))),
                "123456"),
            ("{#blop\n{=you}\n}",
                dict(blop=(dict(you=123), dict(you=456))),
                "123\n456\n"),
            # A join()-like separator.
            ("{#colors {=color}{/comma , }}",
                dict(colors=(dict(color="red"), dict(color="green"),
                     dict(color="blue"))),
                "red, green, blue"),
            # More repeats.
            ("buy {=count} articles: {#articles {=nam} txt {=pri}, }", {
                "count": 2,
                "articles": ({"nam": "Ur", "pri": 1}, {"nam": "Mo", "pri": 2})
                },
                "buy 2 articles: Ur txt 1, Mo txt 2, "),

            ("sell {=count} stocks: {#articles {=nam} &euro; {=pri}{/comma , }}",
                {"count": 2, "articles": ({"nam": "APPL", "pri": 320}, {"nam": "GOOG", "pri": 120})},
                "sell 2 stocks: APPL &euro; 320, GOOG &euro; 120"),
            # Nested repeats.
            ("Contents: {#chapters Chapter {=name}. {#sections Section {=name}. }}",
                {
                    "chapters": [
                        dict(name="Intro", sections=[dict(name="Foreword"), dict(name="Methodology")]),
                        dict(name="Middle", sections=[dict(name="Measuring"), dict(name="Calculation"), dict(name="Results")]),
                        dict(name="Epilogue", sections=[dict(name="Conclusion")])
                        ]
                    },
                "Contents: Chapter Intro. Section Foreword. Section Methodology. "
                "Chapter Middle. Section Measuring. Section Calculation. Section Results. "
                "Chapter Epilogue. Section Conclusion. "),
            # Condition with repeat.
            ("Dear {=name}, {?market Please get the following groceries:\n"
                "{#groceries \tItem: {=item}, {=count} pieces\n}}"
                "{?deadline Please be back before {=time}!}",
                {"name": "Joe",
                 "market": "True",
                 "count": 5,
                 "groceries": [dict(item="lemon", count=2), dict(item="cookies", count=4)],
                 "deadline": True,
                 "time": "17:30",
                 },
                "Dear Joe, Please get the following groceries:\n\tItem: "
                "lemon, 2 pieces\n\tItem: cookies, 4 pieces\nPlease be "
                "back before 17:30!"),

            # Tests for Setter.
            ("{:age 42}The Universe is {=age} years old",
                {},
                "The Universe is 42 years old"),

            # Tests for Counter.
            ("{|nr Shouldnotappear_with_empty_list}",
                {"nr": []},
                ""),
            ("{|nr Shouldappear_with_list_with_only_one_item}",
                {"nr": ["one"]},
                "Shouldappear_with_list_with_only_one_item"),
            ("{|nr Should_not_appear_with_list_of_two_items_or_more}",
                {"nr": ["one", "two"]},
                ""),

            ("{+nr Should_not_appear_with_empty_list}",
                {"nr": []},
                ""),
            ("{+nr Should_not_appear_with_list_with_only_one_item}",
                {"nr": ["one"]},
                ""),
            ("{+nr Should_appear_with_list_of_two_items_or_more}",
                {"nr": ["one", "two"]},
                "Should_appear_with_list_of_two_items_or_more"),

            # Tests voor External.
            #("{$file templates/nl/unittest-helper.tpl}",  # Include mét variabele expansie.
            #    {"age": 42},
            #    "Voor gebruik in unittests van ovotemplate. Het Universum is 42 jaar oud.\n"),
            #
            #("{$verb templates/nl/unittest-helper.tpl}",  # Include zonder variabele expansie, bv. voor Javascript, ivm de { en } tekens.
            #    {"age": 42},
            #    "Voor gebruik in unittests van ovotemplate. Het Universum is {=age} jaar oud.\n"),
            )

        for tems, temv, expected in goodcases:
            tem = Ovotemplate(tems)  # tem.pprint()
            self.assertEqual(tem.render(temv), expected)

        ''' TODO: This still needs some work - sensible error reporting.
        badcases = (
            ("{#a {=b} {=c}}", {}), # required variables missing
            ("=a}", dict(a=42)), # missing opening {
            # ("{=a", dict(a=42)), # missing closing {
            )

        global exceptionless
        exceptionless = False
        for tems, temv in badcases:
            self.assertRaises(Exception, Ovotemplate(tems).render(temv))
        '''

    def test_namedtuple(self):
        import collections
        Entry = collections.namedtuple("Entry", ["name", "telephone"])
        phonebook = [Entry("Mary", "0203898"), Entry("Jan", "0683928")]
        tem = Ovotemplate("{#phonebook {=name} {=telephone}{/sep , }}")
        self.assertEqual(tem.render(dict(phonebook=phonebook)), "Mary 0203898, Jan 0683928")

    def test_bunch(self):
        from bunch import Bunch
        phonebook = [Bunch({"name": "Mary", "telephone": "0203898"}), Bunch({"name": "Jan", "telephone": "0683928"})]
        tem = Ovotemplate("{#phonebook {=name} {=telephone}{/sep , }}")
        self.assertEqual(tem.render(dict(phonebook=phonebook)), "Mary 0203898, Jan 0683928")

    def test_undefined_lenient(self):
        """By default an unknown variable renders as nothing, like Jinja2's Undefined."""
        from bunch import Bunch, DefaultBunch

        self.assertEqual(Ovotemplate("[{=missing}]").render({}), "[]")
        self.assertEqual(Ovotemplate("[{=missing}]").render({"other": 1}), "[]")
        # Same for objects, whether or not they invent a value for unknown attributes.
        self.assertEqual(Ovotemplate("[{=missing}]").render(Bunch(other=1)), "[]")
        self.assertEqual(Ovotemplate("[{=age}]").render(DefaultBunch(name="Joe")), "[]")
        # An explicit None renders as nothing too - note that Jinja2 writes "None" here.
        self.assertEqual(Ovotemplate("[{=v}]").render({"v": None}), "[]")
        # Conditions treat a missing name as False, so {!name ...} means "if not set".
        self.assertEqual(Ovotemplate("{?missing J}{!missing N}").render({}), "N")
        # Repeating over a missing name yields nothing rather than an error.
        self.assertEqual(Ovotemplate("[{#missing {=v}}]").render({}), "[]")
        # A missing name does not swallow the rest of the template.
        self.assertEqual(Ovotemplate("A{=missing}B").render({}), "AB")

    def test_envflag(self):
        """Boolean settings can be taken from the environment, so a deployment can
        enable strict variables without a code change."""
        name = "OVOTEMPLATE_TEST_FLAG"
        previous = os.environ.get(name)
        try:
            os.environ.pop(name, None)
            self.assertFalse(envflag(name))
            self.assertTrue(envflag(name, default=True))  # Unset falls back to the default.
            for true in ("1", "true", "TRUE", "Yes", "on", " on "):
                os.environ[name] = true
                self.assertTrue(envflag(name), "%r should read as true" % true)
            for false in ("0", "false", "no", "off", "", "banana"):
                os.environ[name] = false
                self.assertFalse(envflag(name), "%r should read as false" % false)
                self.assertFalse(envflag(name, default=True), "%r should override the default" % false)
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    def test_undefined_strict(self):
        """With strictvars a typo in a variable name surfaces, like Jinja2's StrictUndefined."""
        global strictvars, exceptionless
        previousstrict, previousexc = strictvars, exceptionless
        try:
            strictvars = True
            self.assertIn("Template error", Ovotemplate("{=missing}").render({}))
            self.assertIn("Template error", Ovotemplate("{#missing {=v}}").render({}))
            # Known variables are of course still rendered normally.
            self.assertEqual(Ovotemplate("{=v}").render({"v": "x"}), "x")
            # Conditions stay lenient even here, so {!name ...} keeps working.
            self.assertEqual(Ovotemplate("{?missing J}{!missing N}").render({}), "N")
            # With exceptionless off, the same case raises instead.
            exceptionless = False
            self.assertRaises(KeyError, Ovotemplate("{=missing}").render, {})
        finally:
            strictvars, exceptionless = previousstrict, previousexc

    def test_advance(self):
        """advance() walks a (line, column) position over a piece of text."""
        self.assertEqual(advance(1, 1, ""), (1, 1))
        self.assertEqual(advance(1, 1, "abc"), (1, 4))
        self.assertEqual(advance(1, 1, "ab\n"), (2, 1))
        self.assertEqual(advance(1, 1, "ab\ncd"), (2, 3))
        self.assertEqual(advance(4, 7, "\n\n"), (6, 1))

    def test_positions_in_tree(self):
        """Every compiled node remembers where in the source it was written."""
        tem = Ovotemplate("Hoi {=naam},\n{?korting {=pct}%}", "offerte.tpl")
        lit, sub, comma, cond = tem.root
        self.assertEqual((lit.line, lit.column), (1, 1))  # "Hoi "
        self.assertEqual((sub.line, sub.column), (1, 5))  # the { of {=naam}
        self.assertEqual((comma.line, comma.column), (1, 12))  # ",\n"
        self.assertEqual((cond.line, cond.column), (2, 1))  # the { of {?korting
        # Text chopped off the head token keeps its own position, past name and space.
        innersub = cond[1]
        self.assertEqual((innersub.line, innersub.column), (2, 11))  # the { of {=pct}
        # The template name travels along, and where() renders the lot.
        self.assertEqual(sub.templatename, "offerte.tpl")
        self.assertEqual(sub.where(), "offerte.tpl, line 1, column 5")
        self.assertIn("@ offerte.tpl, line 1, column 5", repr(sub))
        # A container built by hand has no position and says nothing about one.
        self.assertEqual(Container("handmade").where(), "")
        self.assertNotIn("@", repr(Container("handmade")))

    def test_positions_in_errors(self):
        """Errors point at the spot in the template where the mistake was made."""
        global strictvars, exceptionless
        previousstrict, previousexc = strictvars, exceptionless
        try:
            strictvars = True

            def message(tems, vars=None, name="offerte.tpl"):
                return Ovotemplate(tems, name).render(vars if vars is not None else {})

            # An unknown variable, in a substitution and in a repetition.
            self.assertIn('in Sub at offerte.tpl, line 2, column 8: unknown variable "typo"',
                          message("regel1\nregel2 {=typo}"))
            self.assertIn('in Rep at offerte.tpl, line 3, column 3: unknown variable "lijstt"',
                          message("a\nb\n  {#lijstt {=v}}"))
            # Nested constructs report their own position, not their parent's.
            self.assertIn('in Sub at diep.tpl, line 3, column 6: unknown variable "naaam"',
                          message("{?ok\n  {#items\n     {=naaam}}}", {"ok": 1, "items": [{}]}, "diep.tpl"))
            # A bad metachar, and both flavours of unbalanced brace.
            self.assertIn("at offerte.tpl, line 2, column 3: '{' without a following valid metachar",
                          message("x\ny {&bad}"))
            self.assertIn("unbalanced '{' at offerte.tpl, line 2, column 6",
                          message("een\ntwee {?c drie", {"c": 1}))
            self.assertIn("unbalanced '}' at offerte.tpl, line 2, column 5",
                          message("een\ntwee}"))
            # An unnamed template still reports line and column.
            self.assertIn("at line 2, column 8: unknown variable", Ovotemplate("regel1\nregel2 {=typo}").render({}))

            # The same positions reach the exceptions when exceptionless is off.
            exceptionless = False
            try:
                Ovotemplate("x\ny {&bad}", "offerte.tpl")
            except ValueError as e:
                self.assertIn("line 2, column 3", str(e))
            else:
                self.fail("expected a ValueError")
            try:
                Ovotemplate("een\ntwee {?c drie", "offerte.tpl")
            except UnbalancedBrace as e:
                self.assertEqual((e.line, e.column), (2, 6))
                self.assertIn("offerte.tpl, line 2, column 6", str(e))
            else:
                self.fail("expected an UnbalancedBrace")
            try:
                Ovotemplate("{=typo}", "offerte.tpl").render({})
            except KeyError as e:
                self.assertIn("line 1, column 1", str(e))
            else:
                self.fail("expected a KeyError")
        finally:
            strictvars, exceptionless = previousstrict, previousexc

    def test_autoescape(self):
        """Substituted values are HTML-escaped, so context data cannot inject markup."""
        evil = '<script>alert("xss")</script>'
        escaped = '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
        self.assertEqual(Ovotemplate("{=v}").render({"v": evil}), escaped)
        # Ampersands too, and inside repetitions and conditions.
        self.assertEqual(Ovotemplate("{=v}").render({"v": "Jip & Janneke"}), "Jip &amp; Janneke")
        self.assertEqual(Ovotemplate("{?c {=v}}").render({"c": True, "v": evil}), escaped)
        self.assertEqual(Ovotemplate("{#l {=v}}").render({"l": [{"v": evil}]}), escaped)
        # Literal text in the template is the author's own markup and stays untouched.
        self.assertEqual(Ovotemplate("<b>&euro; {=v}</b>").render({"v": "5 & 6"}), "<b>&euro; 5 &amp; 6</b>")
        # Numbers are unaffected.
        self.assertEqual(Ovotemplate("{=v}").render({"v": 42}), "42")

    def test_autoescape_optout(self):
        """{=!name} inserts markup verbatim, and so does anything already marked Raw."""
        markup = "<b>bold</b>"
        self.assertEqual(Ovotemplate("{=!v}").render({"v": markup}), markup)
        self.assertEqual(Ovotemplate("{=v}").render({"v": Raw(markup)}), markup)
        # A setter captures template markup, so reading it back must not double-escape.
        self.assertEqual(Ovotemplate("{:x <b>{=v}</b>}{=x}").render({"v": "a & b"}), "<b>a &amp; b</b>")
        # The global switch turns escaping off altogether.
        global autoescape
        previous = autoescape
        try:
            autoescape = False
            self.assertEqual(Ovotemplate("{=v}").render({"v": markup}), markup)
        finally:
            autoescape = previous

    def test_unbalanced_braces(self):
        """A missing brace is reported instead of being silently tolerated."""
        global exceptionless
        previous = exceptionless
        try:
            exceptionless = False
            self.assertRaises(UnbalancedBrace, Ovotemplate, "start {?c yes")
            self.assertRaises(UnbalancedBrace, Ovotemplate, "{#a {=b}")
            self.assertRaises(UnbalancedBrace, Ovotemplate, "no opening brace}")

            exceptionless = True
            for bad in ("start {?c yes", "{#a {=b}", "no opening brace}"):
                self.assertIn("Template error", Ovotemplate(bad).render({"c": True}))
            # Balanced templates are of course unaffected.
            self.assertEqual(Ovotemplate("{?c {#a {=b}}}").render({"c": True, "a": [{"b": "x"}]}), "x")
        finally:
            exceptionless = previous

    def test_properties(self):
        """Computed properties on the context object are usable as template variables,
        in substitutions as well as in conditions and repetitions."""
        from bunch import Cursus

        def cursus(**kwargs):
            defaults = dict(aantaldagen=3, aanschafeenheid="dag", aanschafeenheid_meervoud="dagen", lesvorm_naam="klassikaal")
            defaults.update(kwargs)
            return Cursus(**defaults)

        # A property in a substitution.
        klassikaal = cursus()
        self.assertEqual(Ovotemplate("{=duration}").render(klassikaal), "3 dagen")
        self.assertEqual(Ovotemplate("{=duration_long}").render(klassikaal), "3 trainingsdagen")

        # Properties driving the branches of a condition.
        tem = Ovotemplate("{?toon_trainingsvormen VORMEN}{?custom_cta CTA}")
        self.assertEqual(tem.render(klassikaal), "VORMEN")
        self.assertEqual(tem.render(cursus(lesvorm_naam="coaching")), "CTA")

        # A property returning None renders as nothing, like any other empty variable.
        self.assertEqual(Ovotemplate("[{=custom_taal}]").render(klassikaal), "[]")
        self.assertEqual(
            Ovotemplate("[{=custom_taal}]").render(cursus(lesvorm_naam="scan")),
            "[De scan kan eventueel verzorgd worden in het Engels.]")

        # Properties survive being reached through a repetition.
        tem = Ovotemplate("{#cursussen {=lesvorm_naam}: {=duration}{/sep ; }}")
        self.assertEqual(
            tem.render(dict(cursussen=[cursus(), cursus(aantaldagen=1, lesvorm_naam="scan")])),
            "klassikaal: 3 dagen; scan: 1 dag")

        # Plain methods are NOT exposed; they must not leak into the output as a
        # repr of a bound method, whichever undefined-mode is in force.
        self.assertEqual(Ovotemplate("{=get}").render(klassikaal), "")
        self.assertEqual(Ovotemplate("{=items}").render({"a": 1}), "")
        global strictvars
        previous = strictvars
        try:
            strictvars = True
            self.assertIn("Template error", Ovotemplate("{=get}").render(klassikaal))
            self.assertIn("Template error", Ovotemplate("{=items}").render({"a": 1}))
        finally:
            strictvars = previous


def acquire(context, pathelems, usebraces=True):
    fn = os.path.join("templates", *pathelems) + ".tpl"
    tpl = Ovotemplate(usebraces=usebraces).fromfile(fn)
    return tpl.render(context)


def test_performance(nr=40, repeats=5):
    """Ovotemplate and Jinja2 go head-to-head, escaping on both sides.

    The comparison is only fair if both engines do the same work: Jinja2's bare
    Template() does not escape, while Ovotemplate does since autoescape landed,
    so this measures both engines in both modes. Compilation happens up front;
    only rendering is timed, best of `repeats` runs.

    Measured on an Apple Silicon laptop, Python 3.14.7 / Jinja2 3.1.6,
    nr=40 (64000 sections, 8.98 MB of output):

        Ovotemplate  escaping off   0.074 sec
        Jinja2       escaping off   0.054 sec
        Ovotemplate  escaping on    0.111 sec
        Jinja2       escaping on    0.101 sec

    So the two are within about ten percent of each other once both escape.
    An earlier note here claimed Ovotemplate was over twice as fast (467MB in
    66.237 sec against Jinja2's 156.205 sec on a MacBook Air); Jinja2 compiles
    templates to Python bytecode and has closed that gap since. Escaping costs
    Ovotemplate roughly half again as much time, which is 37 ms on 9 MB.
    """
    import time
    from jinja2 import Template

    books = []
    d = {"books": books}
    for booknr in range(nr):
        chapters = []
        book = dict(title="%d bottles of beer" % booknr, toc="This will be the table of contents.", chapters=chapters)
        books.append(book)
        for chapternr in range(nr):
            sections = []
            chapter = dict(title="%d. How to drink beer" % chapternr, intro="This will be an intro", sections=sections)
            chapters.append(chapter)
            for sectionnr in range(nr):
                section = dict(title="%d. Procedure" % sectionnr, text="This will be an explanation of how to drink beer.")
                sections.append(section)

    ovo = Ovotemplate("""
        {#books
            <h1>The Book Of {=title}</h1>
            <p>{=toc}</p>
            {#chapters
                <h2>Chapter {=title}</h2>
                <p>{=intro}</p>
                {#sections
                    <h3>Section {=title}</h3>
                    <p>{=text}</p>
                }
            }
        }
        """)
    jinjasource = """
        {% for book in books %}
            <h1>The Book Of {{book.title}}</h1>
            <p>{{book.toc}}</p>
            {% for chapter in book.chapters %}
                <h2>Chapter {{chapter.title}}</h2>
                <p>{{chapter.intro}}</p>
                {% for section in chapter.sections %}
                    <h3>Section {{section.title}}</h3>
                    <p>{{section.text}}</p>
                {% endfor %}
            {% endfor %}
        {% endfor %}
        """

    def fastest(render):
        "Best of `repeats` runs, to squeeze out scheduling noise."
        best = None
        for _ in range(repeats):
            start = time.perf_counter()
            result = render()
            elapsed = time.perf_counter() - start
            if best is None or elapsed < best:
                best, output = elapsed, result
        return best, output

    global autoescape
    previous = autoescape
    runs = []
    try:
        for escaping in (False, True):
            autoescape = escaping
            runs.append(("Ovotemplate", escaping) + fastest(lambda: ovo.render(d)))
            jinja = Template(jinjasource, autoescape=escaping)
            runs.append(("Jinja2", escaping) + fastest(lambda: jinja.render(books=books)))
    finally:
        autoescape = previous

    print("%d sections, %.2f MB of output, best of %d" % (nr ** 3, len(runs[0][3]) / 1024 / 1024, repeats))
    for engine, escaping, elapsed, output in runs:
        print("    %-12s escaping %-3s %7.3f sec" % (engine, "on" if escaping else "off", elapsed))
    if nr < 3:
        print(runs[0][3])


if __name__ == "__main__":
    # For the usual unittests:
    unittest.main()

    # Uncomment for a benchmark comparison:
    # test_performance()

    # res = Ovotemplate("Hello {=name}").render(dict(name="Jan"))
    # print(res)

