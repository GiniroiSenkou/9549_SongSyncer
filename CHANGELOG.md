# Changelog

## Unreleased

### Fixed

- **Edit Song / Assign Tags froze for several seconds the first time** — the tag-icon loader used to re-encode every icon in `Data/Tags/` through a slow, unnecessary PIL pass on first use. If it still happens, an icon file may be unusually large; consider shrinking it
- **Saving in Edit Song freezes for a couple of seconds on a large library, sometimes crashing on Close** — on a library of a few thousand songs, the JSON Sync Check audit (and the per-tag icon lookups it triggered a refresh of) used to run synchronously on the UI thread after every save, which could take several seconds and leave the window unresponsive; closing it while frozen could queue up an unhandled state. The audit now runs on the background reconcile thread, icon lookups are properly cached, and the "missing JSON entries" list is only rebuilt when it actually changes
- **A deleted "Missing Entries" row keeps coming back** — deleting one of two JSON entries that happened to have identical content (same title, same tags — common for untagged entries) could remove the wrong one, since the lookup matched by value instead of by the specific entry. The one you actually selected now always gets removed
- **Renaming a song in Edit Song seemed to leave the old name behind and add a new entry instead of renaming** — this happened for a song with an unmerged case/spacing-drifted JSON duplicate (e.g. "AC dc - X.mp3" and "AC DC - X.mp3" both matching the same file): renaming only updated one of the two records, so the other — now matching nothing — reappeared as a fresh "missing" entry under the old name. Renaming now merges any duplicate into the record before renaming it, so it's always exactly one rename, never a new file or a new entry
- **Sync Check listed every song as "No lyrics file" right after a scan** — the background audit started before the lyrics folder had been indexed. Lyrics are now indexed first.
- **Grid view showed placeholder covers after using the list view** — thumbnails already loaded for the list never reached grid cards built later; they are now re-queued when a card is created.
- **Selected tags in Edit Song were unreadable** — the stylesheet suppressed the highlight, leaving white text on no fill. Selected tags are now painted with their blue background.
- **Edit Song showed no cover until the song's thumbnail had been loaded** — the cover is now read from the file when needed.
