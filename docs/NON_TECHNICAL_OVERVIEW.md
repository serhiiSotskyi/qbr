# Non-Technical Overview

## What this system does

This project turns marketing spreadsheet exports into a ready-made quarterly PowerPoint report.

Instead of building the deck by hand, the system:

1. reads the latest campaign performance data
2. picks the most recent full quarter
3. calculates the key numbers and trends
4. optionally adds search demand and competitor data
5. writes short commentary
6. builds the slides automatically

## In simple terms

Think of it as an automated reporting assistant.

You give it:

- a performance spreadsheet
- optionally a Google Trends export
- optionally an Auction Insights export

It gives you:

- charts
- summary tables
- commentary bullets
- recommendations
- a finished PowerPoint report

## What happens behind the scenes

The process is straightforward:

1. The system checks the files and makes sure the data is usable.
2. It looks for the latest quarter where all three months are available.
3. It groups the data into views like overall performance, campaign type, and destination.
4. It compares results with the same quarter last year when that data exists.
5. It turns the numbers into charts and short plain-English points.
6. It places everything into the report template and saves the final deck.

## Why this is useful

- saves time on manual quarterly reporting
- keeps reporting format consistent across clients
- reduces copy-paste mistakes
- makes it easier to produce the same style of report every quarter
- gives teams a faster starting point for review and discussion

## What it does not do

- it does not replace strategy or final human review
- it does not invent insights from outside the uploaded data
- it does not automatically clean or correct badly structured source files

The system is best used as a fast first draft generator for quarterly client reporting.
