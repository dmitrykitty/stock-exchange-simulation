# Propagacja szoków surowcowych w agentowym rynku giełdowym z grafem zależności aktywów

**Propozycja projektu badawczego — przedmiot „Symulacja systemów dyskretnych”, AGH**
**Zespół:** _(imiona i nazwiska — 4 osoby)_
**Wersja:** 28 października 2026 r.

---

## Streszczenie

Projekt dotyczy budowy agentowej symulacji rynku giełdowego, w której ceny akcji osiemnastu rzeczywistych spółek amerykańskich nie są zadawane z zewnątrz, lecz powstają w wyniku transakcji zawieranych przez około tysiąc agentów w arkuszach zleceń (*limit order book*) z regułą *price-time priority*. Jedynym wejściem ze świata rzeczywistego w trakcie symulacji są sygnały zewnętrzne: ceny surowców (ropa, gaz, miedź, złoto) oraz indeks S&P 500. Agenci dysponują wiedzą o zależnościach między aktywami w postaci ważonego grafu: struktura grafu pochodzi z tabel przepływów międzygałęziowych BEA (*input-output*), a wagi — z regresji dziennych stóp zwrotu z lat 2021–2023. Agenci wyceniający aktualizują swoje wyceny, obserwując zmiany cen „rodziców” w grafie na symulowanej giełdzie, dzięki czemu szok cenowy surowca rozchodzi się falą od spółki do spółki **poprzez transakcje**, a nie poprzez z góry zaprogramowaną formułę. Celem badania jest ustalenie, jak skład strategii agentów, zachowania stadne i koncentracja kapitału wpływają na przenoszenie szoków wzdłuż łańcuchów zależności (kompletność, opóźnienie, przestrzelenie) oraz na powstawanie współruchu spółek niepowiązanych w grafie. Model zostanie zweryfikowany testami silnika giełdowego, a jego zachowanie porównane z rzeczywistymi danymi z lat 2024–2025 za pomocą faktów stylizowanych, rekonstrukcji grafu z symulowanych cen i pasm niepewności z wielu przebiegów.

**Słowa kluczowe:** symulacja agentowa, symulacja zdarzeń dyskretnych, arkusz zleceń, graf zależności, przepływy międzygałęziowe, zachowania stadne, propagacja szoków, współruch, fakty stylizowane.

---

## 1. Wprowadzenie i motywacja

Ceny aktywów finansowych reagują na zdarzenia w gospodarce realnej nie tylko bezpośrednio, ale także pośrednio — poprzez łańcuchy dostaw i powiązania między branżami. Wzrost ceny ropy podnosi koszty rafinerii, rafinerie podnoszą ceny paliw, linie lotnicze ponoszą wyższe koszty paliwa. Na rynku giełdowym takie zależności są przenoszone przez decyzje inwestorów, którzy działają z opóźnieniem, na podstawie niepełnych informacji i często naśladują innych. W efekcie reakcja cen może być niepełna, spóźniona lub przesadzona, a szok może „przeskoczyć” na spółki, których z pierwotnym zdarzeniem nic nie łączy.

Modelowanie agentowe pozwala odtworzyć ten mechanizm bezpośrednio: zamiast zakładać gotową zależność cen, modelujemy uczestników rynku, ich informacje, zasoby i reguły decyzyjne, a ceny wynikają z ich interakcji. Projekt spełnia wymagania przedmiotu: system złożony z oddziałujących agentów, własny model (a nie gotowy framework), część obliczeniowa oraz wizualizacja z możliwością zmiany parametrów w trakcie działania symulacji.

Wartość projektu ma trzy wymiary:
- **poznawczy:** zbadanie, jak lokalne reguły uczestników rynku przekładają się na propagację szoków w sieci aktywów;
- **metodologiczny:** pełny cykl od definicji modelu przez kalibrację na danych rzeczywistych do weryfikacji, eksperymentów i oceny niepewności;
- **inżynierski:** wydajny silnik giełdowy w C++ z kontrolerem w Pythonie, odtwarzalne eksperymenty i interaktywna wizualizacja.

## 2. Kontekst badawczy

**Punkt wyjścia.** Skrzyński (2025) przedstawia wieloagentowe środowisko rynku (inwestorzy, brokerzy, giełda z regułami GPW) zaimplementowane w Javie z użyciem JADE. Szczególny „wyimaginowany inwestor” o nieograniczonych zasobach składa zlecenia wokół historycznych cen akcji, przez co cena podąża za danymi rzeczywistymi; przy dużej „mocy” tego inwestora pozostali uczestnicy praktycznie nie wpływają na cenę. W opisanym eksperymencie bada się strategię pojedynczego inwestora na jednym aktywie (AAPL). W publicznym repozytorium autora strategie fundamentalna, trendowa i losowa pozostają nieukończone.

**Modele agentowe rynku.** Lux i Marchesi (1999) pokazali, że interakcja fundamentalistów i chartystów generuje własności szeregów cenowych (grube ogony, grupowanie zmienności), których nie zawiera proces napływu informacji. Chiarella i Iori (2002) analizowali agentów o zróżnicowanych strategiach w arkuszu zleceń. Symulator ABIDES (Byrd i in., 2019) łączy symulację zdarzeń dyskretnych z modelem giełdy i agentów.

**Wiele aktywów i zarażanie.** Westerhoff (2004) oraz Chiarella, Dieci i He (2007) rozszerzają modele heterogenicznych agentów na wiele powiązanych rynków. Kyle i Xiong (2001) opisują zarażanie przez efekt majątkowy wspólnych inwestorów, a Barberis, Shleifer i Wurgler (2005) — współruch cen przekraczający fundamentalne powiązania.

**Sieci gospodarcze.** Acemoglu i in. (2012) pokazują, że szoki rozchodzą się przez sieć powiązań międzygałęziowych. Tabele przepływów międzygałęziowych (Leontief; Miller i Blair, 2009) opisują, ile każda branża kupuje od każdej innej.

## 3. Luka badawcza i wkład własny

| | Skrzyński (2025) | Niniejszy projekt |
|---|---|---|
| Rola danych rzeczywistych | **wymuszają cenę** (wyimaginowany inwestor) | sygnały zewnętrzne dla agentów i źródło ich wiedzy (wagi grafu); **cena powstaje endogenicznie** |
| Aktywa | 1 | 18 spółek + 4 surowce + indeks |
| Zależności między aktywami | brak | graf zależności: struktura z tabel BEA, wagi z regresji |
| Agenci | 1–2 | ~1000, kilka typów strategii, zachowania stadne |
| Metodyka | pojedynczy przebieg | siatki parametrów × 30 ziaren losowych, ablacje, walidacja na okresie odłożonym |

Wkładem projektu jest **model, w którym graf zależności stanowi wiedzę agentów, a propagacja szoku jest zjawiskiem emergentnym**: fala przechodzi od spółki do spółki przez rzeczywiście zawierane transakcje, więc błędy rynku (np. przestrzelenie ceny jednej spółki) są przenoszone dalej. Nie zakładamy pierwszeństwa poszczególnych mechanizmów w literaturze; oryginalność dotyczy ich połączenia, kalibracji i zaprojektowanych eksperymentów.

## 4. Cel, pytania badawcze i hipotezy

**Cel główny.** Zbadać, w jaki sposób skład strategii agentów, zachowania stadne i koncentracja kapitału wpływają na stabilność rynku oraz na przenoszenie rzeczywistych szoków surowcowych wzdłuż grafu zależności aktywów.

| Kod | Pytanie badawcze | Status |
|---|---|---|
| P1 | Jak siła zachowań stadnych wpływa na obsunięcia cen i dostępność płynności? | podstawowe (1 aktywo) |
| P2 | Jak koncentracja kapitału w funduszach wpływa na stabilność rynku? | podstawowe (1 aktywo) |
| P3–P4 | Czy efekty P1 i P2 są addytywne i które reguły lokalne wzmacniają lub wygaszają ruchy cen? | dodatkowe |
| **P5** | **Jak skład strategii i zachowania stadne zmieniają propagację szoku wzdłuż grafu: kompletność, opóźnienie i przestrzelenie względem trajektorii referencyjnej?** | **główne** |
| **P6** | **Czy powstaje współruch spółek niepowiązanych w grafie i który kanał za niego odpowiada: wspólny portfel, rebalansowanie, limity ryzyka, zachowania stadne?** | **główne** |
| P7 | Jaki udział agentów znających zależności między spółkami jest potrzebny, aby fala przechodziła, i czy powoduje on przestrzelenie? | eksploracyjne |

**Hipotezy robocze:**
- **H1:** wzrost siły zachowań stadnych zwiększa obsunięcia i wydłuża okresy jednostronnego arkusza zleceń;
- **H2:** przy stałych łącznych zasobach większa koncentracja kapitału zwiększa wpływ pojedynczych uczestników na cenę;
- **H3:** większy udział strategii trendowych i silniejsze zachowania stadne zwiększają przestrzelenie oraz wzmacniają falę na kolejnych ogniwach łańcucha;
- **H4:** wspólny portfel i rebalansowanie generują nadmiarowy współruch spółek niepowiązanych w grafie; efekt znika przy rozdzielonych portfelach;
- **H5:** wzrost udziału agentów sieciowych skraca opóźnienie przenoszenia szoku.

Hipotezy mogą zostać odrzucone lub potwierdzone tylko w części zakresu parametrów; brak efektu także jest wynikiem.

## 5. Model

### 5.1 Architektura i czas

Symulacja zdarzeń dyskretnych: kolejka zdarzeń uporządkowana według czasu (decyzje agentów, wygasanie zleceń, migawki arkusza, początek sesji, zdarzenia sterujące). Jedna sesja symulacji odpowiada jednemu dniowi danych; wewnątrz sesji agenci działają w losowych momentach, dlatego fala w grafie przebiega w obrębie dnia — zgodnie z danymi rzeczywistymi, w których wczorajsza zmiana ceny „rodzica” nie prognozuje dzisiejszej zmiany „dziecka” (Valero → Delta: współczynnik 0,025, t = 0,8). Wszystkie losowania pochodzą z generatorów inicjalizowanych ziarnem; ta sama konfiguracja i ziarno dają identyczny przebieg.

### 5.2 Giełda i bramka

Każda spółka ma własny arkusz zleceń. Zlecenia z limitem (GTC, IOC, GTD); zlecenie rynkowe to IOC z ceną ochronną. Dopasowywanie według ceny, a przy równej cenie według kolejności; cena transakcji to cena zlecenia oczekującego. Zlecenie, które dopasowałoby się z własnym zleceniem agenta, jest odrzucane. Ceny i środki są liczbami całkowitymi (ticki i centy). Bramka (*gateway*) rezerwuje środki przed wysłaniem zlecenia i rozlicza obie strony transakcji; łączna ilość pieniędzy i akcji jest stała (niezmiennik sprawdzany testami).

### 5.3 Graf zależności

Węzły: sygnały zewnętrzne (surowce, indeks) i spółki. Krawędź $j \to i$ z wagą $w_{ij}$ oznacza: zmiana ceny $j$ o 1% zmienia uczciwą wycenę $i$ o $w_{ij}$%.

- **Struktura** — z tabel BEA: krawędź „dostawca → odbiorca” istnieje, jeśli branża odbiorcy wydaje na produkty branży dostawcy co najmniej ustalony udział produkcji (próg 2%). Przykłady (2023): rafinacja wydaje 50,8% produkcji na ropę i gaz; transport lotniczy 12,9% na produkty naftowe.
- **Wagi** — z regresji dziennych logarytmicznych stóp zwrotu (2021–2023) z indeksem rynku jako zmienną kontrolną:

$$
r_{i,t} = \alpha_i + \sum_{j \in \text{parents}(i)} w_{ij}\, r_{j,t} + \beta^{\text{mkt}}_i\, r^{\text{mkt}}_t + u_{i,t}
$$

Przykładowe wyniki (752 dni): ropa → ConocoPhillips 0,52 (t = 20); ConocoPhillips → Valero 0,70 (t = 20); Valero → Delta 0,22 (t = 6,5), przy czym bezpośredni wpływ ropy na Delta jest nieistotny (−0,04), a po uwzględnieniu Valero staje się ujemny (−0,14). Dlatego szacujemy dwa zestawy wag: **bezpośrednie** (tylko sygnały zewnętrzne) i **pełne** (także spółki). Warunek stabilności: promień spektralny macierzy wag między spółkami < 1.

### 5.4 Agenci

| Typ | Reguła |
|---|---|
| Wyceniający krótkowzroczny | aktualizuje wycenę na podstawie sygnałów zewnętrznych (wagi bezpośrednie) |
| Wyceniający sieciowy | uwzględnia także **ceny rynkowe** spółek-rodziców w grafie (wagi pełne) |
| Trendowy | ekstrapoluje ostatnią zmianę ceny |
| Szumowy | decyzje losowe |
| Animator rynku | kwotuje obie strony przy ograniczonych zasobach |
| Fundusz rebalansujący | utrzymuje docelowe udziały w portfelu |
| Limit ryzyka | sprzedaje portfel po przekroczeniu progu straty |

Aktualizacja wyceny agenta $a$ dla spółki $i$ w chwili decyzji $t$ (poprzednia decyzja w $t'$):

$$
\ln V_{a,i}(t) = \ln V_{a,i}(t') + \sum_{j \in N_a(i)} \hat w_{a,ij}\,\Big[\ln P^{\text{obs}}_{a,j}(t) - \ln P^{\text{obs}}_{a,j}(t')\Big]
$$

gdzie $P^{\text{obs}}_{a,j}(t) = P_j(t - d_a)$ to cena obserwowana z opóźnieniem $d_a$ (dla spółek — cena **na symulowanej giełdzie**), a $\hat w_{a,ij} = w_{ij}(1 + \eta_{a,ij})$ to waga z indywidualnym błędem. Sygnał $b = \operatorname{clip}\big((V - P)/(\gamma P), -1, 1\big)$ jest mieszany z sygnałami znajomych w sieci obserwacji: $s_i = (1 - h)\, b_i + h\, \bar d_{N_i}$, gdzie $h$ to siła zachowań stadnych. Wspólny portfel agenta obejmuje wszystkie aktywa.

### 5.5 Trajektoria referencyjna

Miarą „poprawnej” reakcji jest trajektoria $R$, którą otrzymalibyśmy, gdyby wszyscy agenci natychmiast i dokładnie zastosowali pełne wagi grafu:

$$
\ln R_i(t) - \ln R_i(0) = \sum_{j \in \text{parents}(i)} w_{ij}\,\big[\ln R_j(t) - \ln R_j(0)\big], \qquad R_k = X_k \text{ dla sygnałów zewnętrznych}
$$

Agenci jej nie znają; służy wyłącznie do pomiarów.

## 6. Dane

| Dane | Źródło | Rola |
|---|---|---|
| Ropa WTI i Brent, gaz Henry Hub, S&P 500 (dzienne) | FRED | sygnały zewnętrzne, zmienna kontrolna |
| Miedź, złoto (dzienne kontrakty terminowe) | Yahoo Finance | sygnały zewnętrzne |
| Ceny miesięczne surowców | Bank Światowy (Pink Sheet) | weryfikacja kontraktów terminowych |
| Ceny i wolumeny ~18 spółek | Yahoo Finance | wagi grafu, ceny startowe, walidacja |
| Tabele przepływów międzygałęziowych (71 branż, 1997–2023) | BEA | struktura grafu |
| Kody SIC spółek | SEC EDGAR | przypisanie spółek do branż |
| 49 portfeli branżowych, czynnik rynkowy | Kenneth French Data Library | walidacja |

Wagi szacujemy na latach **2021–2023**, symulację i porównanie z rzeczywistością prowadzimy na latach **2024–2025**. Rok 2020 (pandemia, ujemna cena ropy) pomijamy. Wszystkie źródła zostały sprawdzone pod kątem dostępności.

## 7. Metodyka

### 7.1 Eksperymenty

| Kod | Opis | Czynniki | Przebiegi |
|---|---|---|---|
| E1 | 1 aktywo | $h \in \{0;\ 0{,}3;\ 0{,}6\}$ × koncentracja $C \in \{0{,}2;\ 0{,}5;\ 0{,}8\}$ × 30 | 270 |
| E2–E3 | ablacje, wrażliwość na animatorów rynku | — | ~200 |
| E4 | łańcuch ropa → COP → VLO → DAL, syntetyczny szok ±20% | skład agentów (3) × $h$ (3) × 30 | 540 |
| E5 | pełna sieć, rzeczywiste sygnały 2024–2025 | skład agentów (3) × $h$ (3) × 30 | 270 |
| E6 | współruch: szok jednej spółki, obserwacja spółki niepowiązanej | portfel wspólny / rozdzielny × $h$ (2) × 30 | 120 |
| E7 | udział agentów sieciowych | {0; 25%; 50%} × 30 | 90 |

### 7.2 Miary

- **stabilność:** maksymalne obsunięcie, udział czasu z jednostronnym arkuszem, spread, głębokość, wpływ zlecenia na kwotowania;
- **propagacja (względem $R$):** kompletność przeniesienia $\Delta \ln P / \Delta \ln R$, opóźnienie (przesunięcie maksymalizujące korelację wzajemną), przestrzelenie, okres półtrwania błędu wyceny, wzmocnienie wzdłuż łańcucha;
- **współruch nadmiarowy:** $\operatorname{corr}(r^P_i, r^P_j) - \operatorname{corr}(r^R_i, r^R_j)$; fałszywe krawędzie — pary istotnie skorelowane bez ścieżki w grafie.

### 7.3 Analiza statystyczna

Jednostką porównania jest pełny przebieg, nie pojedyncza transakcja. Dla każdej konfiguracji raportujemy rozkład miar (wykresy pudełkowe z punktami przebiegów) i różnice względem wariantu kontrolnego z 95-procentowymi przedziałami ufności z bootstrapu po przebiegach. Protokół (miary, rozgrzewka, długość, ziarna) zostaje zamrożony po pilotażu; badanie główne korzysta z nowych ziaren.

### 7.4 Weryfikacja i walidacja

- **Weryfikacja:** testy kontraktowe silnika (priorytet ceny i czasu, częściowe wykonania, anulowanie, samohandel, zachowanie pieniędzy i akcji), testy różnicowe implementacji C++ względem referencyjnej implementacji w Pythonie, determinizm przebiegów.
- **Walidacja (2024–2025):** fakty stylizowane (grube ogony, brak autokorelacji stóp zwrotu, grupowanie zmienności, związek wolumenu ze zmiennością), brak prognozowalności z opóźnieniem jednodniowym, rekonstrukcja grafu z symulowanych cen, pasma 90% z wielu przebiegów, zgodność kierunku reakcji na duże ruchy ropy, porównanie macierzy korelacji. Fakty stylizowane sprawdzamy także dla przebiegów bez sygnałów zewnętrznych, aby oddzielić efekty agentów od własności danych wejściowych.

Projekt nie służy prognozowaniu cen konkretnych spółek.

## 8. Narzędzia i architektura

| Element | Technologia |
|---|---|
| Silnik giełdowy (arkusze, dopasowywanie) | C++20, moduł Pythona przez nanobind, budowa scikit-build-core + CMake |
| Implementacja referencyjna, bramka, symulacja, agenci | Python 3.11+, NumPy |
| Dane i analiza | pandas, pyarrow (Parquet), notatniki Jupyter |
| Testy | pytest, hypothesis (testy różnicowe), Catch2 |
| Wizualizacja | Plotly (odtwarzanie przebiegów i tryb na żywo) |
| Organizacja pracy | Git + GitHub (pull requesty, CI), kontrakty interfejsów między modułami |

Moduły komunikują się przez zamrożone kontrakty: giełda ↔ symulacja, dane ↔ pozostałe moduły, graf i sygnały zewnętrzne, format logu przebiegu, format konfiguracji eksperymentu. Pozwala to czterem osobom pracować równolegle.

**Wizualizacja.** Tryb odtwarzania (wykres świecowy z wolumenem, arkusz zleceń, sygnały agentów, graf z „falą” zmian cen, porównanie ceny z trajektorią referencyjną) oraz tryb na żywo z przyciskami szoków surowcowych i suwakiem siły zachowań stadnych; każda zmiana jest zapisywana jako zdarzenie.

## 9. Harmonogram

| Etap | Tydzień | Kryterium zakończenia |
|---|---|---|
| M0 | 1 | kontrakty uzgodnione; referencyjna giełda działa; dostęp do danych potwierdzony; wstępny graf |
| M1 | 3 | jedno aktywo: przebieg → log → miary; silnik C++ zgodny z implementacją referencyjną |
| M2 | 4 | eksperymenty E1–E3 (pytania P1–P4) |
| M3 | 6 | pełna sieć na rzeczywistych sygnałach 2024–2025 |
| M4 | 9 | eksperymenty E4–E7, walidacja, wizualizacja |
| — | 10 | raport i prezentacja |

## 10. Oczekiwane rezultaty i kryteria sukcesu

1. Działający symulator z wydajnym silnikiem giełdowym, odtwarzalnymi przebiegami i trybem wsadowym.
2. Graf zależności ~18 spółek skalibrowany na danych rzeczywistych, z udokumentowanym uzasadnieniem każdej krawędzi.
3. Odpowiedzi na pytania P1–P6 z oceną niepewności, także w postaci „brak efektu w badanym zakresie”.
4. Walidacja: co model odtwarza, a czego nie.
5. Interaktywna wizualizacja propagacji szoków.

Sukcesem jest poprawna implementacja i wiarygodna odpowiedź na pytania badawcze; nie wymaga to potwierdzenia hipotez.

## 11. Ryzyka i ograniczenia

| Ryzyko / ograniczenie | Postępowanie |
|---|---|
| Słabe wagi części krawędzi (0,1–0,2) | wynik raportowany uczciwie; mechanizm demonstrowany na łańcuchu o silnych wagach |
| Puste arkusze przy wielu aktywach | animator rynku na każde aktywo; ewentualnie 10 spółek zamiast 18 |
| Wydajność | silnik C++, zlecenia wsadowe, równoległe przebiegi |
| Wagi z cen historycznych zawierają zachowanie rzeczywistego rynku | traktujemy je jako przekonania agentów, nie jako „prawdę o gospodarce” |
| Indeks S&P 500 zawiera część badanych spółek | uproszczenie opisane w raporcie; ablacja bez indeksu |
| Jedna spółka — jedna branża; stałe zależności w czasie; dane z USA | ograniczenia opisane w raporcie |

Poza zakresem: krótka sprzedaż, kredyt, instrumenty pochodne, dywidendy i emisje, agenci uczący się, prognozowanie cen.

## Bibliografia

1. Skrzyński, J. (2025). *Multi-Agent Simulation of Financial Markets: Micro and Macro Approach*. W: R. Doroz, B. Zielosko (red.), *Progress in Polish Artificial Intelligence Research 6*, s. 335–341. Wydawnictwo Uniwersytetu Śląskiego. [BaDAP](https://badap.agh.edu.pl/publikacja/165799); [kod](https://github.com/j-skrzynski/MASfSES-JADE).
2. Lux, T., Marchesi, M. (1999). Scaling and criticality in a stochastic multi-agent model of a financial market. *Nature*, 397, 498–500. [DOI: 10.1038/17290](https://doi.org/10.1038/17290).
3. Chiarella, C., Iori, G. (2002). A simulation analysis of the microstructure of double auction markets. *Quantitative Finance*, 2(5), 346–353.
4. Byrd, D., Hybinette, M., Balch, T. H. (2019). ABIDES: Towards High-Fidelity Market Simulation for AI Research. [arXiv:1904.12066](https://arxiv.org/abs/1904.12066).
5. Westerhoff, F. (2004). Multiasset market dynamics. *Macroeconomic Dynamics*, 8(5), 596–616.
6. Chiarella, C., Dieci, R., He, X.-Z. (2007). Heterogeneous expectations and speculative behavior in a dynamic multi-asset framework. *Journal of Economic Behavior & Organization*, 62(3), 408–427.
7. Kyle, A. S., Xiong, W. (2001). Contagion as a Wealth Effect. *Journal of Finance*, 56(4), 1401–1440.
8. Barberis, N., Shleifer, A., Wurgler, J. (2005). Comovement. *Journal of Financial Economics*, 75(2), 283–317.
9. Acemoglu, D., Carvalho, V. M., Ozdaglar, A., Tahbaz-Salehi, A. (2012). The Network Origins of Aggregate Fluctuations. *Econometrica*, 80(5), 1977–2016.
10. Miller, R. E., Blair, P. D. (2009). *Input-Output Analysis: Foundations and Extensions* (2nd ed.). Cambridge University Press.
11. Sharpe, W. F. (1963). A Simplified Model for Portfolio Analysis. *Management Science*, 9(2), 277–293.
12. Cont, R. (2001). Empirical properties of asset returns: stylized facts and statistical issues. *Quantitative Finance*, 1(2), 223–236.
13. Źródła danych: [FRED](https://fred.stlouisfed.org/), [BEA Input-Output Accounts](https://www.bea.gov/industry/input-output-accounts-data), [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Kenneth French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html), [World Bank Commodity Markets](https://www.worldbank.org/en/research/commodity-markets), Yahoo Finance.
