# Week 3 Writeup: Document Q&A with RAG

## What I Built

I built `ask.py`, a command-line retrieval-augmented generation (RAG) tool that answers questions from a folder of company documents. It loads every `.html`, `.htm`, `.md`, `.txt`, and `.pdf` file in the folder, splits each document into chunks, and stores the chunks in a Chroma vector database. For each question it retrieves the four most similar chunks by cosine similarity and sends only those chunks to Claude (`claude-sonnet-5`), which answers from them and cites the source file for every fact.

The `--docs` option names the document folder, and without it the tool uses `week-03/copperline-docs`. Each document folder gets its own Chroma database under `week-03/chroma_db/<folder name>/`, so the Copperline and Larkfield collections never mix, and a database is rebuilt automatically whenever the files in its folder change. The databases and the Python environment (`.venv`) are excluded from Git in `week-03/.gitignore`. The `--save` option writes the complete output of a run to a UTF-8 text file: the retrieved chunks first, each with its rank, similarity score, and source file, then a line containing only `ANSWER`, then Claude's answer. I used it to answer six questions about Larkfield Credit Union's member-services documents, saved in `runs/` (`q1-before.txt`, `q1-after.txt`, and `q2.txt` through `q6.txt`).

## Why This Approach

Chunking method: I split each document at its h1, h2, and h3 headings so that every chunk is one policy section, any section longer than 1,500 characters is split again at paragraph or sentence boundaries, and PDFs are split by page.

Makes sense on its own: Each chunk begins with its section heading, so a chunk such as "Wire transfers" followed by the list of wire fees can be understood and cited without the rest of the document, and site banners and footers are dropped so they never become chunks.

Label and source: Every chunk is stored in Chroma with its source file name and section heading as metadata, and each run prints the rank, similarity score, and source file of every chunk, so each answer can be traced back to the exact document it came from.

Short enough: The Larkfield chunks average about 260 characters and the longest is 649, well under the 1,500-character cap, so the top four chunks give Claude several complete rules without burying the relevant sentence in unrelated text.

## Example Run

Command (run from `week-03`):

```
.venv\Scripts\python ask.py --docs larkfield-docs --save runs\q4.txt "Can I refund the outgoing international wire transfer fee for a member without approval from a manager?"
```

The full contents of `runs/q4.txt`:

```text
Rank: 1 | Similarity: 0.6016 | Source: fee-refund-guide.html
Refunds that need a branch manager
A refund needs the branch manager's approval when the fee is more than $30, when the member has already had a refund in the last 90 days, or when the member asks for several fees to be refunded at once. A branch manager may approve refunds of up to $150 in total for one member in any 90-day period.

Rank: 2 | Similarity: 0.5836 | Source: consumer-fee-schedule.html
Wire transfers
Incoming domestic wire transfer: $10. Incoming international wire transfer: $15. Outgoing domestic wire transfer: $25. Outgoing international wire transfer: $45. The receiving bank or an intermediary bank may deduct its own fees from the amount sent.

Rank: 3 | Similarity: 0.5504 | Source: fee-refund-guide.html
What a member service representative can approve
A member service representative may approve a refund of a single fee of $30 or less without anyone else's approval. A representative may approve only one refund for the same member in any 90-day period.

Rank: 4 | Similarity: 0.4914 | Source: fee-refund-guide.html
Fee Refund Guide for Member Service Staff
Version 2 (current). Owner: Member Services.

ANSWER
No. The outgoing international wire transfer fee is $45 [consumer-fee-schedule.html]. A member service representative can only approve refunds of fees $30 or less without additional approval [fee-refund-guide.html]. Since $45 exceeds that limit, branch manager approval is required, as refunds needing approval include any fee more than $30 [fee-refund-guide.html].
```

Neither document answers this question alone: the fee schedule gives the $45 fee but says nothing about refunds, and the refund guide gives the $30 limit but lists no fees. Retrieval brought back both pieces, and Claude combined them into the correct answer.

## Reflection

What was wrong: Question 1 retrieved the overdraft section from both consumer-fee-schedule.html (Version 8, $29) and the outdated consumer-fee-schedule-v7.html (Version 7, $35) at nearly the same similarity, 0.6982 and 0.6857, so Claude reported that the documents disagreed instead of giving the current fee.

How I fixed it: I moved consumer-fee-schedule-v7.html out of the indexed folder into a separate larkfield-docs-archive folder, rebuilt the Larkfield Chroma database from 45 chunks down to 35, and reran Question 1, which then answered $29 per item citing only the current schedule.

Prevention: Only current policies should live in the folder the tool indexes, so whenever a policy is replaced the old version should be moved to the archive at the same time, and the database then rebuilds automatically because the folder changed.

Question 6: The question asked for the current 30-year fixed mortgage rate, which no Larkfield document covers, and although retrieval still returned four chunks, their similarity was only 0.19 to 0.27 and Claude said the documents did not answer the question instead of passing off a share certificate rate as a mortgage rate.
