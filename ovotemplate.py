#!/usr/bin/env python3

# TODO: String-only mode
# TODO: in template ook members kunnen accessen: {=cursus.lesvorm}, scheelt weer.
# TODO: bij tokens en Lits en Conds etc, de regel/charpos van de positie in source template onthouden, makkelijker debuggen.

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

verbose = False
exceptionless = True  # False: throw exceptions when something is wrong with the template or rendering it; True: insert an error in the output text instead.
autoescape = True  # True: HTML-escape substituted values; use {=!name} or a Raw() value to insert markup verbatim.
strictvars = False  # False: an unknown variable renders as nothing (like Jinja2's Undefined); True: it is reported (like Jinja2's StrictUndefined).

MISSING = object()  # Sentinel: distinguishes "no default given" from a default of None.


def indent(level):
    return "| " + "    " * level


class Raw(str):
    """A string that is already markup and must never be escaped again.
    Setters produce these, so that {:x <b>hi</b>}{=x} keeps working under autoescape."""


class UnbalancedBrace(Exception):
    "A template has an opening brace without a matching closing one, or the other way round."


def escape(value):
    "HTML-escape a substituted value, unless it is explicitly marked as Raw."
    if isinstance(value, Raw):
        return value
    return html.escape(value, quote=True)


def errorspan(msg):
    "Render a template error as a conspicuous inline span."
    return '<span class="ovotemplate_error" style="background-color: red; color: white;">%s</span>' % msg


def undefined(kind, name):
    """Decide what an unknown variable renders as.
    Lenient by default, like Jinja2's Undefined: a missing name simply produces nothing,
    which keeps optional fields out of the template. Set strictvars=True for Jinja2's
    StrictUndefined behaviour, where a typo in a variable name surfaces instead of
    silently blanking part of the page."""
    if not strictvars:
        return ""
    if not exceptionless:
        raise KeyError(name)
    return errorspan('Template error in %s: unknown variable "%s"' % (kind, name))


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

    def __init__(self, name="", usebraces=True):
        self.name = name
        self.usebraces = usebraces

    def __repr__(self):
        tag = "%s %s" % (self.__class__.__name__, self.name)
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
            output = errorspan('Template error in External: "%s" is an unknown external source-type' % self.type)
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
            return undefined("Sub", self.name)
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
            return undefined("Rep", self.name)

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
    """Split input into tokens. A token is either an open brace, a closing brace, or a string without braces."""
    tokens = []
    token = ""
    for c in it:
        if c == openbrace:
            if token:
                tokens.append(token)
                token = ""
            tokens.append(c)
        elif c == closebrace:
            if token:
                tokens.append(token)
                token = ""
            tokens.append(c)
        else:
            token += c
    if token:
        tokens.append(token)
    return tokens


def parse(it, node, openbrace, closebrace, nesting=0):
    """Build a (recursive) nested list from the tokens."""
    for token in it:
        if token == openbrace:
            subnode = []
            node.append(subnode)
            parse(it, subnode, openbrace, closebrace, nesting + 1)
        elif token == closebrace:
            if nesting == 0:
                raise UnbalancedBrace("Unbalanced '%s'" % closebrace)
            return
        else:
            node.append(token)
    if nesting:
        # Ran out of tokens while still inside a construct.
        raise UnbalancedBrace("Unbalanced '%s'" % openbrace)


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


def compile(node, into, usebraces, openbrace, closebrace, level=0):
    if verbose:
        print("%s compile: " % indent(level), node)
    for pos, item in enumerate(node):
        if isinstance(item, list):
            if verbose:
                print("%s #%d list: %r" % (indent(level), pos, item))
            head = item[0] if item else ""
            if not head or not head[0] in createinfo:
                msg = "'%s' without a following valid metachar" % openbrace
                if exceptionless:
                    into.append(Lit(errorspan("Template error: " + msg)))
                    continue  # Report this one construct, but keep compiling the rest of the template.
                else:
                    raise ValueError(msg)
            first, rest = splitfirst(head)
            operator, name = first[0], first[1:]
            if verbose:
                print("%s operator %s, name %s, rest %r" % (indent(level), operator, name, rest))
            # Create correct container
            factoryfunc, options = createinfo[operator]
            ob = factoryfunc(name, usebraces=usebraces)
            if options == CHOPNAME:
                item[0] = rest
            elif options == CHOPITEM:
                item = item[1:]
            into.append(compile(item, ob, usebraces, openbrace, closebrace, level + 1))
        else:
            if verbose:
                print("%s #%d item: %s" % (indent(level), pos, item))
            into.append(Lit(item))
    return into


def process(sourcetext, usebraces, openbrace, closebrace):
    if verbose:
        print("\n\n\nCompile phase")
    tokens = lexer(feed(sourcetext), openbrace, closebrace)
    # root = Container()
    root = []
    try:
        parse(feed(tokens), root, openbrace, closebrace)
    except UnbalancedBrace as e:
        if not exceptionless:
            raise
        result = Container(usebraces=usebraces)
        result.append(Lit(errorspan("Template error: %s" % e)))
        return result
    result = compile(root, Container(usebraces=usebraces), usebraces, openbrace, closebrace)
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
        if s:
            self.root = process(s, self.usebraces, self.openbrace, self.closebrace)
        elif s is not None:
            self.root = Container(usebraces=self.usebraces)
            self.root.append(Lit(""))
        else:
            self.root = None
        self.name = name

    def fromfile(self, fn):
        """Load a template from a file.
        Allows: tem = Ovotemplate().fromfile("hello.tpl")
        The template file should contain UTF-8 encoded unicode text
        """
        with open(fn) as f:
            tpl = f.read()
        self.root = process(tpl, self.usebraces, self.openbrace, self.closebrace)
        self.name = fn.replace(" ", "_")
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


def test_performance():
    """Ovotemplate and Jinja2 go head-to-head!
    Result for nr=150 on my MacBook Air:
        Ovotemplate: 467MB produced in 66.237 sec
        Jinja2: 470MB produced in 156.205 sec
    """
    import time
    nr = 2
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
    tem = Ovotemplate("""
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
    start = time.time()
    res = tem.render(d)
    dur = time.time() - start
    print("Ovotemplate: %dMB produced in %.3f sec:" % (len(res) / 1024 / 1024, dur))
    if nr < 3:
        print(res)
    #
    from jinja2 import Template
    tem = Template("""
        {% for book in books %}
            <h1>The Book Of {{book.title}}</h2>
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
        """)
    start = time.time()
    res = tem.render(dict(books=books))
    dur = time.time() - start
    print("Jinja2: %dMB produced in %.3f sec:" % (len(res) / 1024 / 1024, dur))
    if nr < 3:
        print(res)


if __name__ == "__main__":
    # For the usual unittests:
    unittest.main()

    # Uncomment for a benchmark comparison:
    # test_performance()

    # res = Ovotemplate("Hello {=name}").render(dict(name="Jan"))
    # print(res)

