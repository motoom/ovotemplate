"""
:mod:`bunch`
------------

Module om dicts op een object-achtige manier te kunnen gebruiken;
d.w.z, met 'd.name' ipv. 'd["name"]' een attribuut kunnen benaderen.
"""


# Ik heb een dict {"name": "Jan", "age": 27}
#
# ...die ik graag naar een object wil omzetten zodat de keys attributes worden, zodat ik kan zeggen:
#
# print "%s is %d jaar oud" % (p.name, p.age)


class Bunch(object):
    """Object wrapper voor een dict."""

    def __init__(self, initial=None, **kwargs):
        if isinstance(initial, Bunch):
            self.__dict__.update(dict(initial.__dict__))
        elif isinstance(initial, dict):
            self.__dict__.update(initial)
        if kwargs:
            self.__dict__.update(kwargs)

    def __getitem__(self, key):
        """Makes subscriptability working (name=person["firstname"])"""
        return self.__dict__[key]

    def __setitem__(self, key, value):
        """Makes item assignment possible (person["age"]=27)"""
        self.__dict__[key] = value

    def __len__(self):
        return len(self.__dict__)

    def __repr__(self):
        return repr(self.__dict__)

    def __str__(self):
        s = ""
        for k in sorted(self.__dict__.keys()):
            v = self.__dict__[k]
            if isinstance(v, str):
                if len(v) > 50:
                    v = v[:50] + "..."
            s += "%s: %r\n" % (k, v)
        return s.strip()

    def update(self, other):
        if isinstance(other, Bunch):
            self.__dict__.update(other.__dict__)
        else:
            self.__dict__.update(other)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


class DefaultBunch(Bunch):
    """Same as Bunch, but returns None for lookups of non-existant attributes."""

    def __getattr__(self, key):
        return None


def bunched(dicts):
    """Return een tuple van Bunch objects, gegeven een collectie van dicts."""
    return tuple(Bunch(d) for d in dicts)


if __name__ == "__main__":
    # TODO: make proper unittest

    q = DefaultBunch(name="Joe")
    print(q.name, q.age)

    d = dict(firstname="Joe", lastname="Doe", age=27, phone="06-8239.43.12", email="joe@zom.com")
    p = Bunch(d)
    print("-" * 80)
    print("%s is %d years old" % (p.firstname, p.age))
    print("-" * 80)
    print("%(firstname)s is %(age)d years old" % p)
    print("-" * 80)
    print(p)
    print("-" * 80)
    print(repr(p))
    print("-" * 80)
    print(p.firstname)  # access as attribute
    print(p["firstname"])  # access like a dict
    print("-" * 80)
    p["taste"] = "sweet"  # item assignment
    p.aftertaste = "bitter"
    print(p)
    print("-" * 80)
    add = dict(length=1.87, weigth=82)
    p.update(add)
    print(p)
    print("-" * 80)

    basis = Bunch(a=1, b=2)
    extradict = dict(c=3, d=4)
    extrabunch = Bunch(e=5, f=6)
    basis.update(extradict)
    basis.update(extrabunch)
    print(basis)

    # Copy
    orig = Bunch()
    orig.a = 1
    copy = Bunch(orig)
    copy.a = 2
    assert orig.a != copy.a
    assert orig is not copy
    print(orig.a, copy.a)

    ''' method chaining, nog eens uitzoeken
    extra = Bunch(c=3, d=4)
    chained = Bunch(a=1, b=2).update(extra)
    assert chained.d == 4
    '''

    ds = (
        dict(name="Joe", age=27),
        dict(name="Sue", age=21),
        dict(name="Peter", age=56)
    )
    for p in bunched(ds):
        print("%s is %d years" % (p.name, p.age))
        print("-" * 80)

    ds = ()
    for p in bunched(ds):
        print("%s is %d years" % (p.name, p.age))
        print("-" * 80)

    n = Bunch(None)
    t = Bunch(dict(a=1))
    f = Bunch(dict())
    k = Bunch(a=1, b=2, c=3)

    if n:
        print("wrong")
    if f:
        print("wrong")
    if not t:
        print("wrong")
    if k.b != 2:
        print("wrong")

    e = Bunch()
    print(e)
    print("-" * 80, bool(e))
    e.color = "yellow"
    print(e)
    print("-" * 80, bool(e))

    # Recursion
    j = Bunch(name="Jane", age=23)
    k = Bunch(name="Kay", age=22)
    j.spouse = k
    k.spouse = j
    print(str(j))
    print(str(k))
