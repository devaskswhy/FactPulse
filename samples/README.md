# Sample documents

Drop the assignment's starter PDFs here. They are gitignored (they may be
large, and they are not ours to redistribute), but the pipeline reads them
from this path.

Ingest one:

    curl -F "file=@samples/<name>.pdf" http://127.0.0.1:8000/documents

Ingest all of them:

    for f in samples/*.pdf; do curl -F "file=@$f" http://127.0.0.1:8000/documents; done

Pick two that overlap in topic (e.g. two Delhivery documents, or two India
macroeconomy documents) so the relationship engine has something to compare.
