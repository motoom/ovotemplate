# Ovotemplate

Een templating-engine van één bestand, zonder dependencies, met een syntaxis
die in één tabel past.

```python
>>> from ovotemplate import Ovotemplate
>>> Ovotemplate("Hallo {=naam}!").render({"naam": "Jan"})
'Hallo Jan!'
```

Geen `{% for %}` / `{% endfor %}`, geen environments, geen loader-configuratie.
Eén metateken achter de accolade zegt wat er gebeurt, en de sluitaccolade zegt
waar het ophoudt. Dat is de hele taal.

## Waarom

De meeste templating-engines zijn gebouwd voor mensen die een templating-taal
wíllen. Deze is gebouwd voor het tegenovergestelde geval: je hebt HTML, je hebt
een dict, en je wilt er zo min mogelijk tussen. Het hele pakket is `ovotemplate.py`
— 1275 regels, inclusief 17 unittests en een benchmark. De imports zijn `os`,
`pprint`, `re`, `html`, `unittest`, `functools` en `datetime`. Meer niet.

## De hele syntaxis

| Constructie | Betekenis | Voorbeeld | Resultaat |
|---|---|---|---|
| `{=naam}` | substitutie | `Hallo {=naam}!` | `Hallo Jan!` |
| `{=!naam}` | substitutie zonder escaping | `{=!html}` | `<b>vet</b>` |
| `{?naam …}` | als waar | `{?lid Welkom terug}` | `Welkom terug` |
| `{!naam …}` | als niet waar | `{!lid Word lid!}` | `Word lid!` |
| `{#naam …}` | herhaal | `{#kleuren {=naam}}` | `roodgroenblauw` |
| `{#naam:a:b …}` | herhaal een deel | `{#kleuren:0:2 {=naam}}` | `roodgroen` |
| `{/naam …}` | scheiding, valt weg na de laatste | `{#kleuren {=naam}{/s , }}` | `rood, groen, blauw` |
| `{:naam …}` | leg vast in een variabele | `{:btw 21}Tarief: {=btw}%` | `Tarief: 21%` |
| `{\|naam …}` | als de lijst precies één item heeft | `{\|items Eén stuk}` | `Eén stuk` |
| `{+naam …}` | als de lijst er meer dan één heeft | `{+items Meerdere}` | `Meerdere` |
| `{$file pad}` | voeg een template in, mét expansie | `{$file deel.tpl}` | `Het Universum is 42 jaar oud.` |
| `{$verb pad}` | voeg een bestand in, letterlijk | `{$verb deel.tpl}` | `Het Universum is {=age} jaar oud.` |

Bereiken volgen Python's slice-semantiek, inclusief negatieve grenzen en
clamping: `{#kleuren:-2:x …}` geeft de laatste twee, `{#kleuren:0:99 …}` geeft
gewoon alles. Een `{/scheiding}` binnen een bereik stopt aan het eind van het
bereik, niet aan het eind van de lijst.

Zitten er accolades in je uitvoer — JavaScript, CSS — dan kun je de engine op
guillemets zetten:

```python
Ovotemplate("Hallo «=naam»!", usebraces=False)
```

## Wat je als context mag meegeven

Alles wat een waarde bij een naam kan vinden:

```python
tem = Ovotemplate("{=naam}")

tem.render({"naam": "Mary"})                      # dict
tem.render(Entry(naam="Mary"))                    # namedtuple
tem.render(Bunch(naam="Mary"))                    # bunch.Bunch
tem.render(cursus)                                # elk object
```

Berekende `@property`-members tellen mee. Dat is met opzet: presentatielogica
hoort in je model, niet in je template.

```python
class Cursus(DefaultBunch):
    @property
    def duration(self):
        if self.aantaldagen == 1:
            return f"1 {self.aanschafeenheid}"
        return f"{self.aantaldagen} {self.aanschafeenheid_meervoud}"
```

```python
>>> Ovotemplate("Duur: {=duration}").render(cursus)
'Duur: 3 dagen'
```

Gewone methodes komen er níét doorheen. `{=items}` op een dict levert geen
`<built-in method items>` op, maar niets — of een foutmelding, zie hieronder.

## Escaping

Substituties worden ge-escaped. Je template is HTML; je data is dat niet.

```python
>>> Ovotemplate("{=v}").render({"v": '<script>alert(1)</script>'})
'&lt;script&gt;alert(1)&lt;/script&gt;'
```

Drie manieren om er bewust omheen te gaan, van fijnmazig naar grofmazig:

```python
Ovotemplate("{=!v}").render({"v": markup})     # deze ene substitutie
Ovotemplate("{=v}").render({"v": Raw(markup)}) # deze ene waarde, overal
ovotemplate.autoescape = False                 # helemaal uit
```

Letterlijke tekst in de template blijft ongemoeid — `&euro;` blijft `&euro;` —
en een `{:setter}` legt zijn opgevangen markup vast als `Raw`, zodat
`{:x <b>{=v}</b>}{=x}` niet dubbel escapet.

## Onbekende variabelen

Standaard hetzelfde als Jinja2's `Undefined`: een naam die er niet is, rendert
als niets, is onwaar in een conditie, en levert een lege herhaling op. Dat houdt
optionele velden uit je template.

```python
>>> Ovotemplate("Hallo {=ontbreekt}!").render({})
'Hallo !'
```

Prettig in productie, minder prettig als je een typefout maakt. Daarom is er de
tegenhanger van `StrictUndefined`:

```python
ovotemplate.strictvars = True     # of: OVOTEMPLATE_STRICT=1
```

Condities blijven in beide standen soepel: `{!naam …}` blijft "als dit niet
gezet is" betekenen, want er is geen aparte `is defined`-test.

## Foutmeldingen die zeggen waar

Elke token, elke literal en elke container onthoudt zijn regel en kolom. Een
fout wijst dus naar de plek waar je hem geschreven hebt, en niet naar "ergens":

```
Template error in Sub at offerte.tpl, line 2, column 8: unknown variable "typo"
Template error in Rep at offerte.tpl, line 3, column 3: unknown variable "lijstt"
Template error at offerte.tpl, line 2, column 3: '{' without a following valid metachar
Template error: unbalanced '{' at offerte.tpl, line 2, column 6
```

Ingesloten templates noemen hun eigen bestandsnaam, niet die van de template die
ze insluit.

Standaard verschijnt zo'n melding als een rode `<span>` in de uitvoer, zodat één
kapotte variabele niet de hele pagina meesleurt. Wil je liever een exception:

```python
ovotemplate.exceptionless = False
```

Diezelfde posities maken `pprint()` bruikbaar als debug-gereedschap:

```python
>>> Ovotemplate("{?korting {=pct}%}", "offerte.tpl").pprint()
Container: [Cond korting @ offerte.tpl, line 1, column 1: [Lit @ offerte.tpl,
line 1, column 11: [''], Sub pct @ offerte.tpl, line 1, column 11: [], Lit @
offerte.tpl, line 1, column 17: ['%']]]
```

(Hier afgebroken om te passen; `pprint` zet het op één regel.)

## Instellingen

| Vlag | Standaard | Omgevingsvariabele | Doet |
|---|---|---|---|
| `autoescape` | `True` | `OVOTEMPLATE_AUTOESCAPE` | HTML-escaping van substituties |
| `strictvars` | `False` | `OVOTEMPLATE_STRICT` | onbekende variabelen melden |
| `exceptionless` | `True` | — | foutspan in plaats van exception |
| `verbose` | `False` | — | vertelt luidruchtig wat hij doet |

Toekennen in code wint altijd van de omgeving:

```python
import ovotemplate
ovotemplate.strictvars = settings.DEBUG
```

## Gebruik

```python
from ovotemplate import Ovotemplate

Ovotemplate("Hallo {=naam}!").render({"naam": "Jan"})       # uit een string
Ovotemplate().fromfile("hallo.tpl").render(context)         # uit een bestand
```

Er is ook een `acquire(context, pathelems)` die een template uit `templates/`
haalt en meteen rendert.

## Tests

```
$ python3 -m unittest ovotemplate -v
Ran 17 tests in 0.003s
OK
```

`bunch.py` hoort erbij: de tests gebruiken het voor de context-varianten.

## Snelheid

De benchmark in `test_performance()` beweert dat Ovotemplate ruim tweemaal zo
snel is als Jinja2. Die meting is oud. Op Python 3.14 met Jinja2 3.1.6, 64.000
secties, 3,35 MB uitvoer:

| | escaping uit | escaping aan |
|---|---|---|
| Ovotemplate | 0,069 s | 0,102 s |
| Jinja2 | 0,054 s | 0,097 s |

Bij een eerlijke vergelijking — escaping aan beide kanten aan — is het
tegenwoordig vrijwel gelijkspel. Jinja2 compileert templates naar Python-bytecode
en is daar de afgelopen jaren hard aan blijven werken. Ovotemplate loopt een boom
af en is daarmee ongeveer 10% trager.

Dat is geen reden om de een of de ander te kiezen. Kies Ovotemplate als je één
bestand wilt dat je helemaal kunt lezen; kies Jinja2 als je filters, overerving,
macro's en een ecosysteem wilt.
