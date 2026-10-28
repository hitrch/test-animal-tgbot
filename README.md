# Лісовий порадник — forest advice bot

A Telegram bot for the animals of a fairy-tale forest. Animals upload documents with advice,
other animals ask questions in plain text, and the bot answers in Ukrainian **using only those documents**
and always names the source: the document, the place it belongs to, and who uploaded it and when.

The main quality goal from the customer: **never mix up advice and sources.**
Advice for one mitten (рукавичка) must not leak into answers about another one.

## Quick start

You need Docker with Docker Compose.

1. Get the tokens:
   - **Telegram**: talk to [@BotFather](https://t.me/BotFather), send `/newbot`, copy the token.
   - **Groq**: create a free API key at [console.groq.com/keys](https://console.groq.com/keys).
2. Create `.env` from the template and fill in both tokens:
   ```
   cp .env.example .env
   ```
3. Start everything with one command:
   ```
   docker compose up --build
   ```

On the first start the bot creates the database tables and loads the 78 standard documents
of «Лісовий архів» (a few seconds). Then open your bot in Telegram and send `/start`.

## Using the bot

- **`/start`** — the bot explains the rules: uploaded documents are visible to everyone, no personal data,
  the uploader's name and date are shown in sources. Nothing works until you press **«Погоджуюсь»**.
- **Ask a question** as a normal message and name the place in it:
  «скільки внесок під дубом?», «які гриби біля сосон?».
- **Send a document** with advice: PDF, Word (.docx) or .txt, up to 20 MB.
  The bot finds out which place it is for and what topics it covers.
- **`/my`** — your uploaded documents; you can delete any of them. Other animals' documents cannot be deleted.
- **`/help`** — short instructions.

The bot works in private chats only and ignores groups.

## How it works

### The forest map

Every document belongs to exactly **one place** (12 in total):

| Level | Places |
|---|---|
| Whole forest | весь ліс |
| Forest parts | хвойний, листяний, біля річки |
| Mittens | Під ялиною, У соснах, Біля мурашника (хвойний) · Під дубом, У ліщині (листяний) · Під вербою (листяний **and** біля річки) · Біля бобрової греблі (біля річки) · На узліссі (no part) |

For a question about a place the bot uses the **chain** from the most specific level to the most general one,
e.g. `Під вербою → листяний → біля річки → весь ліс` ([bot/places.py](bot/places.py)).
The more specific advice wins. General advice is mentioned only if it does not contradict it.
If two parts disagree (only possible for Під вербою), the bot shows both with their sources.

### Answering a question

1. **Parse** (small model `openai/gpt-oss-20b`): the question → `{"place": ..., "topics": [...]}` as JSON.
   Topics come from a fixed list ([bot/topics.py](bot/topics.py)).
2. **Search** (plain SQL, no LLM): active documents whose place is in the chain and whose topics overlap.
3. **Answer** (large model `openai/gpt-oss-120b`): the full text of the found documents, each labelled
   with its place and source, plus the priority rules and today's date (much advice is seasonal).

Special cases agreed with the customer:
- no place in the question → advice for the whole forest plus a reminder that places may have their own rules;
  if there is no such advice, the bot asks which place is meant;
- unclear place or a place not on the map → the bot asks, it never guesses;
- nothing found → the bot says so honestly and invents nothing.

### Uploading a document

1. Check the format and the 20 MB limit (the maximum a Telegram bot can download).
2. Extract the text: .txt, .docx, .pdf; scanned PDFs go through Tesseract OCR.
3. The same file again (same SHA-256) → not stored twice.
4. The small model detects the place and topics. Unclear place → the bot asks the uploader with buttons.
   Advice for several places in one file → the bot asks to split it.
5. If the place already has a document on similar topics, the bot shows it and asks with buttons:
   **new version** (the old one stops working but is kept and marked as replaced) or **separate document**
   (both work). Any animal may upload a new version, including of archive documents; only the author may delete.

### «Лісовий архів»

The standard documents live in [data/archive/](data/archive/) and are loaded on startup, only once (by file hash),
so a version replaced by an animal never comes back after a restart.
- Places and topics come from [data/archive/manifest.csv](data/archive/manifest.csv), tagged by hand once.
- 19 PDFs are scans on a birch-bark background. OCR made mistakes in numbers («8 шишки» instead of «3»),
  so their proofread text is stored in [data/archive/ocr/](data/archive/ocr/) and used instead of OCR.

### Errors

- Groq rate limit or network problem → «спробуй ще раз за хвилинку».
- Unsupported file → the bot lists the supported formats.
- File over 20 MB → the bot asks for a smaller one.

## Key decisions

| Decision | Why |
|---|---|
| Search by place + topic filter in SQL, not vector search | Groq has no embeddings API. The place is what matters most for "don't mix up sources", and an exact SQL filter is predictable and testable. |
| Two models | `gpt-oss-120b` writes correct Ukrainian; `gpt-oss-20b` is enough for JSON parsing. Each model has its own rate limit. |
| Postgres | Shared base for all animals, survives restarts, `CHECK` constraint keeps only the 12 valid places. |
| Text stored in the database, original file referenced | OCR takes seconds; it runs once on upload, not on every question. The original stays in Telegram (`file_id`) or in the repo. |
| Archive tagged by a manifest, not by the LLM | Tagging 78 files with the LLM costs ~60K tokens and 7+ minutes because of the 8K tokens/min limit, and file names do not always show the place. |
| Code copied into the image, not mounted | Same result on any machine and a clean clone; no Windows line-ending surprises. |
| aiogram 3, polling | No public URL or HTTPS needed to run the bot. |

## Limits

- Groq free tier: about 8K tokens per minute and 200K per day per model, roughly 80–130 questions a day.
  Models can be changed with `GROQ_ANSWER_MODEL` and `GROQ_PARSE_MODEL` in `.env`.
- OCR of scanned PDFs uploaded by animals is best effort. The customer said photos are not sent.
- Pending upload dialogs (e.g. "new version or separate?") are kept in memory: after a restart the animal
  has to send the file again.

## Project structure

```
bot/
  __main__.py      startup: database, archive, Telegram polling
  config.py        settings from environment variables
  db.py            SQL queries
  schema.sql       tables
  places.py        forest map and place chains
  topics.py        fixed topic list
  extract.py       text from txt / docx / pdf, OCR for scans
  archive.py       loading «Лісовий архів»
  middlewares.py   private chats only, consent check
  texts.py         all texts the bot sends
  handlers/        Telegram handlers by topic
data/archive/      standard documents, manifest.csv, ocr/
docs/dialog.md     the full conversation with the customer
tests/             pytest tests
```

## Tests

Tests use mocks instead of Groq and Telegram, so they need no tokens and no network:

```
pip install -r requirements-dev.txt
pytest
```

## Agreed with the customer

*Customer* — said by Мишка-шкряботушка; *approved* — proposed by me, she agreed.

- Animals upload documents with advice; others ask plain-text questions; the answer always names its source. All users are equal, no admins. *(customer)*
- Ukrainian, simple and friendly. The main goal: never mix up advice and sources. *(customer)*
- Formats: PDF, Word (.docx), .txt; no photos. Up to 20 MB, a bigger file → ask for a smaller one. *(customer / approved)*
- One shared base for the whole forest; private chats only. *(customer)*
- Places: 3 forest parts and 8 mittens from a fixed map; each document belongs to one place; unclear place → ask the uploader. *(customer)*
- Priority: mitten → its forest part → whole forest; general advice only if it does not contradict. *(customer)*
- No place in the question → whole-forest advice plus a reminder; unclear place → ask, never guess. *(approved)*
- Під вербою: if the two parts disagree, show both with sources and warn. *(approved)*
- Same file twice → stored once. New version → the old one stops working; separate document → both work; the uploader chooses with a button. *(customer / approved)*
- Anyone may update a document, including the archive ones; the old version is kept as replaced. Only the author may delete. *(customer / approved)*
- «Погоджуюсь» before anything: documents are public, no personal data, the uploader's name and date are shown. *(approved)*
- Source: the document, its place, and for uploaded ones who sent it and when. *(customer)*
- Not found → say so honestly; errors → «спробуй ще раз за хвилинку». *(approved)*
- «Лісовий архів» from Google Drive must be known from the very start. *(customer)*