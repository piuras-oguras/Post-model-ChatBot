# Post-model-ChatBot

[![CI](https://github.com/piuras-oguras/Post-model-ChatBot/actions/workflows/ci.yml/badge.svg)](https://github.com/piuras-oguras/Post-model-ChatBot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Ollama](https://img.shields.io/badge/Ollama-gpt--oss--safeguard%3A20b-black.svg?logo=ollama&logoColor=white)](https://ollama.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)

Post-model to samodzielny mikroserwis FastAPI, który sprawdza odpowiedź wygenerowaną przez model, zanim trafi ona do użytkownika. Ocena obejmuje cztery kryteria:

- **relevance** – czy odpowiedź dotyczy zadanego pytania,
- **groundedness** – czy opiera się na dostarczonych źródłach,
- **safety** – czy nie zawiera niebezpiecznych treści,
- **leakage** – czy nie ujawnia danych wewnętrznych, np. kluczy API lub haseł.

Oceny dokonuje osobny model-sędzia. Domyślnie jest to `gpt-oss-safeguard:20b` uruchomiony lokalnie w Ollama, ale usługa działa z każdym endpointem zgodnym z OpenAI API.

Integracja sprowadza się do jednego żądania HTTP `POST /v1/check`. W odpowiedzi usługa zwraca jedną z decyzji:

- `allow` – odpowiedź można zwrócić użytkownikowi,
- `regenerate` – odpowiedź jest pusta, nietrafna lub nieoparta na źródłach; należy ją wygenerować ponownie,
- `block` – odpowiedź zawiera niebezpieczne treści lub wyciek danych i nie może zostać zwrócona.

## API

Dokumentacja API jest dostępna pod `/docs`, a schemat OpenAPI pod `/openapi.json`.

| Metoda | Ścieżka     | Autoryzacja          | Opis                             |
| ------ | ----------- | -------------------- | -------------------------------- |
| `GET`  | `/health`   | nie                  | Sprawdzenie, czy usługa działa   |
| `POST` | `/v1/check` | opcjonalna (Bearer)  | Ocena odpowiedzi modelu          |

### Autoryzacja

Jeśli ustawiono zmienną `POSTMODEL__API_TOKEN`, każde żądanie do `/v1/check` musi zawierać nagłówek:

```
Authorization: Bearer <token>
```

Gdy zmienna jest pusta (domyślnie), autoryzacja jest wyłączona.

### `GET /health`

Zwraca `200 OK`, jeśli proces usługi działa. Nie sprawdza dostępności modelu-sędziego.

```json
{ "status": "ok" }
```

### `POST /v1/check`

Ocenia odpowiedź modelu i zwraca decyzję.

#### Żądanie

| Pole                            | Typ     | Wymagane | Domyślnie | Opis                                                        |
| ------------------------------- | ------- | -------- | --------- | ----------------------------------------------------------- |
| `session_id`                    | string  | tak      | –         | Identyfikator sesji, zapisywany w logach                    |
| `user_message`                  | string  | tak      | –         | Pytanie użytkownika                                         |
| `answer`                        | string  | tak      | –         | Odpowiedź wygenerowana przez model, która ma zostać oceniona |
| `retrieved_context`             | array   | nie      | `[]`      | Źródła, na których model oparł odpowiedź                    |
| `retrieved_context[].source_id` | string  | tak      | –         | Identyfikator źródła                                        |
| `retrieved_context[].text`      | string  | tak      | –         | Treść fragmentu źródła                                      |
| `retrieved_context[].metadata`  | object  | nie      | `{}`      | Dowolne metadane                                            |
| `requires_grounding`            | boolean | nie      | `true`    | Czy odpowiedź musi być poparta źródłami                     |

Przy ocenie groundedness model-sędzia dostaje tylko pole `text` każdego źródła. Pola `source_id` i `metadata` nie wpływają na wynik.

```json
{
  "session_id": "abc-123",
  "user_message": "Kiedy otwarta jest biblioteka?",
  "answer": "Biblioteka jest otwarta od 8:00 do 16:00.",
  "retrieved_context": [
    {
      "source_id": "regulamin-biblioteki",
      "text": "Biblioteka jest otwarta od poniedziałku do piątku w godzinach 8:00–16:00.",
      "metadata": { "url": "https://example.edu/biblioteka" }
    }
  ],
  "requires_grounding": true
}
```

#### Odpowiedź

| Pole                   | Typ    | Opis                                                    |
| ---------------------- | ------ | ------------------------------------------------------- |
| `action`               | string | Decyzja: `allow`, `regenerate` lub `block`              |
| `answer`               | string | Tekst, który można pokazać użytkownikowi (patrz niżej)  |
| `violations`           | array  | Wykryte naruszenia; puste przy `allow`                  |
| `violations[].code`    | string | Kod naruszenia (patrz [Kody naruszeń](#kody-naruszeń))  |
| `violations[].reason`  | string | Krótkie uzasadnienie od modelu-sędziego                 |
| `violations[].severity`| string | `low`, `medium` lub `high` (obecnie zawsze `high`)      |

Zawartość pola `answer` zależy od decyzji:

| `action`     | `answer`                                                                                  |
| ------------ |-------------------------------------------------------------------------------------------|
| `allow`      | oryginalna odpowiedź                                                                      |
| `regenerate` | „Nie udało się przygotować odpowiedzi wystarczająco dobrze popartej dostępnymi źródłami.” |
| `block`      | „Nie mogę zwrócić tej odpowiedzi ze względów bezpieczeństwa.”                             |

Przykład `allow`:

```json
{
  "action": "allow",
  "answer": "Biblioteka jest otwarta od 8:00 do 16:00.",
  "violations": []
}
```

Przykład `block`:

```json
{
  "action": "block",
  "answer": "Nie mogę zwrócić tej odpowiedzi ze względów bezpieczeństwa.",
  "violations": [
    {
      "code": "internal_leakage",
      "reason": "Zawiera prawdziwy klucz API.",
      "severity": "high"
    }
  ]
}
```

#### Kody naruszeń

| Kod                | Kiedy występuje                                                                                                                 | Decyzja      |
| ------------------ |---------------------------------------------------------------------------------------------------------------------------------| ------------ |
| `empty_answer`     | `answer` jest pusty lub zawiera same białe znaki; pozostałe testy są wtedy pomijane                                             | `regenerate` |
| `not_relevant`     | odpowiedź nie dotyczy pytania; test jest pomijany, gdy `user_message` jest pusty                                                | `regenerate` |
| `not_grounded`     | odpowiedź zawiera fakty spoza źródeł lub z nimi sprzeczne, albo `retrieved_context` jest pusty przy `requires_grounding: true`  | `regenerate` |
| `unsafe_content`   | odpowiedź zawiera instrukcje mogące wyrządzić poważną szkodę (broń, samobójstwo itp.)                                           | `block`      |
| `internal_leakage` | odpowiedź zawiera sekret: klucz API, token, hasło lub klucz prywatny                                                            | `block`      |


Testy wykonywane są po kolei i każdy z nich to osobne wywołanie modelu-sędziego, czyli do czterech wywołań na jedno żądanie. Limit czasu pojedynczego wywołania ustawia `POSTMODEL__GUARD_MODEL__REQUEST_TIMEOUT_SECONDS` (domyślnie 180 s).

#### Kody statusu HTTP

| Status | Znaczenie                                                                                              |
| ------ | ------------------------------------------------------------------------------------------------------ |
| `200`  | Ocena zakończona; decyzja jest w polu `action`                                                         |
| `401`  | Brak lub nieprawidłowy token (tylko gdy ustawiono `POSTMODEL__API_TOKEN`)                              |
| `422`  | Niepoprawna treść żądania, np. brak wymaganego pola                                                    |
| `502`  | Model-sędzia nie odpowiedział, przekroczył limit czasu lub zwrócił odpowiedź w nieprawidłowym formacie |

## Struktura repozytorium

```
.
├── app/
│   ├── main.py              # aplikacja FastAPI: endpointy, autoryzacja, obsługa błędów
│   ├── output_guard.py      # logika oceny: modele danych, polityki dla sędziego, decyzja
│   ├── guard_model.py       # klient modelu-sędziego
│   └── config.py            # ustawienia ze zmiennych środowiskowych i pliku .env
├── .env.example             # przykładowa konfiguracja
├── Dockerfile               # obraz usługi
├── docker-compose.yaml      # uruchomienie w Dockerze z Ollamą na hoście
├── requirements.txt         # zależności Pythona
├── pyproject.toml
└── uv.lock
```

## Konfiguracja (.env)

Usługa czyta ustawienia ze zmiennych środowiskowych i z pliku `.env` w katalogu, z którego jest uruchamiana. Zmienne środowiskowe mają pierwszeństwo przed `.env`.

Punktem wyjścia jest plik `.env.example`:

```bash
cp .env.example .env
```

| Zmienna                                           | Domyślnie                   | Opis                                                    |
|---------------------------------------------------|-----------------------------|---------------------------------------------------------|
| `POSTMODEL__GUARD_MODEL__BASE_URL`                | `http://localhost:11434/v1` | Adres endpointu zgodnego z OpenAI API                   |
| `POSTMODEL__GUARD_MODEL__API_KEY`                 | `not-needed`                | Klucz API endpointu; Ollama go nie wymaga               |
| `POSTMODEL__GUARD_MODEL__NAME`                    | `gpt-oss-safeguard:20b`     | Nazwa modelu-sędziego                                   |
| `POSTMODEL__GUARD_MODEL__REQUEST_TIMEOUT_SECONDS` | `180.0`                     | Limit czasu jednego wywołania modelu (w sekundach)      |
| `POSTMODEL__API_TOKEN`                            | puste                       | Token Bearer dla `/v1/check`; puste wyłącza autoryzację |
| `POSTMODEL__PORT`                                 | `8011`                      | Port na hoście przy uruchomieniu przez `docker compose` |

Uwagi:

- **Adres i port serwera** ustawia się flagami `--host` i `--port` przy uruchamianiu `uvicorn`. W obrazie Dockera usługa zawsze nasłuchuje na porcie `8011` (ustawionym w `Dockerfile`), a `POSTMODEL__PORT` zmienia tylko port wystawiony na hoście. Aplikacja nie czyta tej zmiennej, używa jej wyłącznie `docker-compose.yaml`.
- **Wymóg oparcia w źródłach** nie jest ustawieniem usługi. Klient podaje go w każdym żądaniu polem `requires_grounding` (domyślnie `true`).
- **Inny dostawca modelu**: wystarczy zmienić `BASE_URL`, `API_KEY` i `NAME`. Model musi stosować się do instrukcji i odpowiadać obiektem JSON `{"violation": ..., "rationale": ...}`. W przeciwnym razie usługa zwraca `502`.

## Uruchomienie

### Wymagania

- Ollama z pobranym modelem-sędzią (ok. 14 GB) albo inny endpoint zgodny z OpenAI API:

```bash
ollama pull gpt-oss-safeguard:20b
```

- Docker z Compose **albo** Python 3.12+.

### Docker Compose (zalecane)

```bash
docker compose up -d --build
```

Usługa jest dostępna pod `http://localhost:8011`. Plik `.env` jest opcjonalny. Bez niego kontener łączy się z Ollamą działającą na hoście pod adresem`http://host.docker.internal:11434/v1` 


Kontener ma healthcheck odpytujący `/health` co 30 s. Status `healthy` oznacza tylko, że działa sama usługa. Nie gwarantuje, że model-sędzia jest osiągalny.

### Lokalnie (bez Dockera)

```bash
python -m venv .venv
source .venv/bin/activate          
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8011
```

### Sprawdzenie działania

```bash
curl http://localhost:8011/health
```

## Przykładowe zapytania

Przykłady zakładają, że usługa działa pod `http://localhost:8011` z wyłączoną autoryzacją. 

Pole `reason` generuje model-sędzia, więc jego treść może się różnić od pokazanej poniżej.

### Odpowiedź poparta źródłami → `allow`

```bash
curl -X POST http://localhost:8011/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo-1",
    "user_message": "Kiedy otwarta jest biblioteka?",
    "answer": "Biblioteka jest otwarta od 8:00 do 16:00.",
    "retrieved_context": [
      {
        "source_id": "regulamin-biblioteki",
        "text": "Biblioteka jest otwarta od poniedziałku do piątku w godzinach 8:00–16:00."
      }
    ]
  }'
```

```json
{
  "action": "allow",
  "answer": "Biblioteka jest otwarta od 8:00 do 16:00.",
  "violations": []
}
```

### Odpowiedź sprzeczna ze źródłami → `regenerate`

```bash
curl -X POST http://localhost:8011/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo-2",
    "user_message": "Ile punktów ECTS potrzeba do ukończenia studiów?",
    "answer": "Do ukończenia studiów potrzeba 210 punktów ECTS.",
    "retrieved_context": [
      {
        "source_id": "regulamin-studiow",
        "text": "Do ukończenia studiów pierwszego stopnia potrzeba 180 punktów ECTS."
      }
    ]
  }'
```

```json
{
  "action": "regenerate",
  "answer": "Nie udało się przygotować odpowiedzi wystarczająco dobrze popartej dostępnymi źródłami.",
  "violations": [
    {
      "code": "not_grounded",
      "reason": "Odpowiedź podaje liczbę punktów ECTS niezgodną z podanymi źródłami.",
      "severity": "high"
    }
  ]
}
```

### Brak źródeł → `regenerate`

Domyślnie `requires_grounding` ma wartość `true`, więc odpowiedź bez `retrieved_context` nie przejdzie oceny groundedness:

```bash
curl -X POST http://localhost:8011/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo-3",
    "user_message": "Kiedy otwarta jest biblioteka?",
    "answer": "Biblioteka jest otwarta od 8:00 do 16:00."
  }'
```

```json
{
  "action": "regenerate",
  "answer": "Nie udało się przygotować odpowiedzi wystarczająco dobrze popartej dostępnymi źródłami.",
  "violations": [
    {
      "code": "not_grounded",
      "reason": "Brak źródeł pozwalających potwierdzić odpowiedź.",
      "severity": "high"
    }
  ]
}
```

Po dodaniu `"requires_grounding": false` to samo żądanie zwraca `allow`. Pozostałe testy (relevance, safety, leakage) nadal są wykonywane.

### Wyciek sekretu → `block`

```bash
curl -X POST http://localhost:8011/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo-4",
    "user_message": "Jak skonfigurować chatbota?",
    "answer": "Wpisz w pliku .env: CHATBOT__MODEL__API_KEY=sk-proj-9fK2mQx7LpR4tZ8vW1nB3cD6eH0jY5sA",
    "requires_grounding": false
  }'
```

```json
{
  "action": "block",
  "answer": "Nie mogę zwrócić tej odpowiedzi ze względów bezpieczeństwa.",
  "violations": [
    {
      "code": "internal_leakage",
      "reason": "Zawiera prawdziwy klucz API.",
      "severity": "high"
    }
  ]
}
```