---
title: 'Foreign Travel: About the Data'
author: '@ryanes'
date: "February 17, 2017"
output: html_document
---

If you haven't already, please review our [readme](https://github.com/Data4Democracy/official-foreign-travel/blob/master/README.md) to learn more about this project and how to contribute.

The purpose of this document is to provide links to the original sources of data that are used in the [Data for Democracy/ProPublica repository](https://github.com/Data4Democracy/official-foreign-travel) `official-foreign-travel`. It also provides descriptions and context for each dataset, including information about the data cleaning methods used. This document will be updated as new datasets are introduced.

## Dataset: Foreign Travel Reports

The original datasets can be downloaded from the [Office of the Clerk](http://clerk.house.gov/public_disc/foreign/index.aspx).

_From Derek Willis of ProPublica:_ 

>House Official Foreign travel reports, which are published quarterly by the House Clerk, are produced either by committees or delegations that are not committee-sponsored. They contain the name of each traveler, arrival and departure dates, the destination, three spending categories (per diem, transportation and other) along with a grand total of money spent (usually in US dollars).

>For committee trips, the name of the committee is in the line beginning `REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL` in the files. Those without a committee might contain `DELEGATION` or an individual's name.

>Caveats: in some cases, the destination is a continent, not a country. This usually happens for trips paid for by the Intelligence Committee. Lawmakers are typically identified by the prefix "Hon" before their names. There could be amended reports, meaning substantially duplicative information would occur. To the extent we can identify those cases, we want to retain the most recent report.

## Processing pipeline (v3)

`oft-download` pulls the raw text files from the House Clerk site into `report_text/`.
`oft-parse` (backed by the `official_foreign_travel.parsing` package) turns them into
structured data:

1. **Segmentation**: each file is split into one table per
   `REPORT OF EXPENDITURES FOR OFFICIAL FOREIGN TRAVEL` header, rather than relying on a
   dashed delimiter that a meaningful fraction of files never contain.
2. **Header parsing**: the sponsor (committee/delegation/commission/individual/etc.) and
   reporting period are extracted from the title line.
3. **Layout detection**: column boundaries (name, arrival, departure, country, and the
   eight cost subcolumns) are detected per table from its own column-header block and
   cross-checked against the real data rows, instead of trusting one fixed set of offsets
   for the whole corpus -- the column positions genuinely differ across the 1994-2019 span.
4. **Row extraction**: travelers, their travel segments, and the four cost categories
   (per diem, transportation, other purposes, total -- each with a foreign-currency and a
   US-dollar-equivalent amount) are pulled out, with wrapped country lists and supplemental
   cost rows ("Commercial airfare," "Delegation Expenses") merged in rather than dropped.
5. **Validation**: each row's costs are checked against its own declared total, and each
   table's rows are checked against its declared committee total. Mismatches are recorded
   as flags, not corrected or hidden -- some of them are genuine errors in the original
   documents.
6. **Deduplication**: reports for the same sponsor and period are treated as the same
   underlying report (keeping the latest publication, per the amended-report caveat above)
   only when one is explicitly marked amended or their traveler rosters substantially
   overlap. Some committees file more than one genuinely distinct report under the same
   generic sponsor label for a single quarter (e.g. separate Appropriations subcommittee
   delegations), so sponsor+period alone isn't a reliable duplicate signal.

The canonical output is JSON (`travel_reports.json`, generated with `oft-parse report_text/
travel_reports.json`): one entry per report, each with its sponsor, period, and travelers,
each traveler with their segments, each segment with resolved dates and the full cost
breakdown. It is not committed to this repository because of its size; regenerate it
locally or via `oft-parse report_text/ output.json --include-superseded` (add
`--include-superseded` to also get amended-report duplicates that would otherwise be
excluded). `travel_report_data.csv` (generated with `oft-parse report_text/
travel_report_data.csv`) is a flattened, one-row-per-segment export in the same column
layout the pre-v3 CSV used, with additional cost/flag columns appended for backward
compatibility. Like the JSON output, it's not committed here -- regenerate it locally.

An optional `--llm-fallback` flag (requires the `llm` extra, Python 3.10+, and whichever
model's credentials `--llm-model` needs -- an Anthropic model by default, or a local/cloud
Ollama model) routes only the small number of tables that fail deterministic parsing to a
model, and re-validates its output against the same arithmetic checks before accepting it --
it is never used for the happy path, and never given a free pass on the invariants. See
[TECHNICAL_README.md](TECHNICAL_README.md) for the full CLI reference.