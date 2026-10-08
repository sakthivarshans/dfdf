# Comparing an extraction against your own data

Extraction tells you what the document says. Comparison answers the question
that usually follows it: **does the document agree with the data we already
hold?**

You upload the spreadsheet you maintain — a price file, a line list, whatever
the document is supposed to reflect — and get back every one of its rows with a
verdict, plus the rows the PDF had that your file does not.

The result is two Excel workbooks, not a score. A percentage tells you a
comparison went badly; these tell you which rows to look at.

## The workflow

The UI (`/ui` → **Extract & Compare**) runs as three pages.

| Step | What happens |
| --- | --- |
| 1 — Upload & Extract PDF | Extract a new PDF, or arrive here from **Compare** on an existing record |
| 2 — Load Existing Data | Upload the `.csv` / `.xlsx` / `.xls` to check against; its columns are read |
| 3 — Compare | Map the fields, run it, download the workbooks |

A step unlocks only once the one before it has produced what it needs, so
Compare cannot be reached without a file to compare against. The stepper badges
navigate back to anything already unlocked.

Comparing from the **All Extractions** list skips straight to step 2 — the
extraction is already done. The bar above the stepper says which one is in play.

## The field mapping

Step 3 asks for two things.

**The unique field** is what pairs a row in the PDF with a row in your file.
Nothing else works: the two sides are in different orders, and a trading pack
does not carry your row numbers.

**The compare fields** are the values that have to agree — one pair per thing
you want checked.

Both are pairs, named independently on each side, because the two files rarely
name a column the same way:

```text
Midas Code      ↔  Code          the unique field
Consumer Deal   ↔  Deal          compare
Std RSP         ↔  RRP           compare
```

The suggestion offered on arrival looks for a column that means the same thing
on both sides and reads like an identifier; failing that, for an
identifier-shaped column on each side independently. **Check it before running**
— a wrong unique field pairs nothing and every row comes back as missing.

### What counts as equal

Keys are matched normalised: trimmed, whitespace collapsed, case-folded. Values
are equal if they agree as text under the same normalisation, or as numbers once
thousands separators and currency symbols are dropped. So `1` / `1.0`,
`1,000` / `1000` and `£1.50` / `1.50` are not reported as OCR errors, while
`ANY 2 FOR £2` / `ANY 2 FOR $2` is.

## The summary

Six counts, and they reconcile:

| Count | Meaning |
| --- | --- |
| Extracted Rows | rows the PDF produced |
| Existing Data Rows | rows in your file |
| Rows Paired | rows of your file found in the PDF by key |
| Mismatched Rows | paired rows where a compared value disagrees |
| Only in Extracted | PDF rows whose key is not in your file |
| Only in Existing | your rows the PDF does not have |

`Rows Paired + Only in Existing == Existing Data Rows`, always. Every row of the
file you uploaded is accounted for in exactly one place — which is the point of
treating your file, rather than the PDF, as the reference list.

There is no accuracy percentage. It is still computed and returned by the API,
and still stored, but it is not shown: a single number invites reading a
comparison as a grade, when the useful output is a list of rows.

### Duplicate keys

If a key appears more than once on either side, the first row wins the lookup
and an amber note says how many were affected. This is not rare — a trading
pack can list the same product in several places — and it is worth reading,
because the rows it skipped were never compared.

## The two workbooks

**Updated Existing Data** is your file returned whole — every row, every column,
untouched — with a verdict appended:

| Column | Value |
| --- | --- |
| `Data in PDF` | `TRUE` when the key was found in the PDF |
| `Status` | `No data in PDF`, `Same Values`, or `Values Mismatch` |
| `Value in PDF` | what the PDF said |

With more than one compared field there is one value column per field
(`Deal in PDF`, `RRP in PDF`) — a single column cannot hold several values.
With exactly one, it is `Value in PDF`. If your file already has a column by one
of those names, the new one is suffixed ` (comparison)` rather than overwriting
your data.

**Data Only in Extracted** is the other direction: the PDF rows, all columns,
whose key never appears in your file. Products added to the document, or a key
the OCR misread.

Both are written to `storage/output`, named after the comparison id, and stay
there — see [deployment.md § Disk usage](deployment.md#disk-usage).

## Limits worth knowing

- **A comparison is only as good as the unique field.** Check the mapping.
- **Duplicate keys are compared once**, on their first row.
- **Blank keys cannot be paired** and are counted separately.
- **The workbooks are written for mapped comparisons only.** An API call with no
  `unique_key` falls back to positional column-name matching and produces no
  files; comparisons made before this feature existed return `404` for both
  downloads.

## Calling it directly

`POST /api/compare` with `unique_key` and `compare_fields`, then
`GET /download/comparison/{id}/existing-data` and `…/extracted-only`. Full
parameters and response shape in [api.md](api.md#post-apicompare).
