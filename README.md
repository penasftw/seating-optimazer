# Seating Optimizer

A small tool that assigns wedding guests to tables using simulated annealing,
based on affinity scores between guests. Started as a favor for my brother's
wedding; currently a personal project, not a commercial product.

**Live app:** https://seating-optimazer.onrender.com

## How it works

1. Guests, groups, and tables live in Firestore, managed through a small
   CRUD API — either entered by hand or bulk-imported from an Excel file
   with three sheets: `Invitados` (guest names, with an optional `Grupo`
   column), `Afinidades` (pairwise affinity scores), and `Mesas` (table
   names, capacities, and types — circular, rectangular, or imperial).
2. The backend runs simulated annealing to find a table assignment that
   maximizes total affinity within each table, factoring in explicit
   pairwise affinities, group cohesion, and clean capacity usage.
3. The result renders as a seating board with draggable guest cards, so
   you can manually fine-tune the algorithm's suggestion, plus dedicated
   views for managing the guest list and groups.
4. Manual changes are saved back to each guest's record, so they survive
   a page refresh.

## Stack

- **Backend:** FastAPI (Python), deployed on Render
- **Frontend:** Vanilla HTML/JS SPA + Tailwind (via CDN), served directly
  by FastAPI, with hash-based routing across three views
- **Persistence:** Firebase Firestore (`guests`, `groups`, `tables`,
  `affinities` collections, plus a single `arrangements/current` snapshot
  document)
- **Optimization:** Simulated annealing over guest-to-table assignments,
  scored by pairwise affinity, implicit group affinity, group
  fragmentation penalty, and a table-capacity packing bonus

## Session log

### Session 1 — manual arrangements stopped disappearing

Starting point: the optimizer worked, but the frontend's drag-and-drop
seating adjustments were never saved anywhere — refreshing the page lost
all manual edits.

**Added:**

- `POST /save-arrangement` and `GET /load-arrangement` endpoints in
  `main.py`, backed by a single Firestore document
  (`arrangements/current`).
- A "Guardar" button in the UI that reads the current board state
  (including manual drag-and-drop changes) and saves it.
- Auto-restore on page load, so the last saved layout reappears without
  needing to re-run the optimizer.
- Firebase Admin SDK initialization that supports **two** credential
  sources (see architecture decisions below).

**Also resolved along the way:**

- A GitHub push protection block, caused by `serviceAccountKey.json`
  being committed to git. The key was never actually exposed (push
  protection rejected it before it reached GitHub's servers), but it was
  revoked and regenerated anyway as a precaution.
- A `.gitignore` location bug: the ignore file lived inside `Algoritmo/`
  while the key file had been moved to the repo root, so the ignore rule
  never applied. Fixed by moving `.gitignore` to the repo root and the
  key file back next to `main.py`.
- `plantilla_invitados.xlsx` is still tracked in git — flagged as
  something to keep as a generic template only, since real guest lists
  (names, relationships, affinity scores) shouldn't live in git history.

### Session 2 — guest/group CRUD, group-aware scoring, full SPA rebuild

Starting point: guests and affinities only existed for the duration of a
single Excel upload — there was no way to manage a guest list, groups,
or tables independently, and the frontend was a single-view board with
no navigation.

**Backend (`main.py`, `seating_optimizer.py`):**

- Replaced the single-document arrangement store with real Firestore
  collections: `guests`, `groups`, `tables`, `affinities`, alongside the
  existing `arrangements/current` snapshot.
- Full CRUD for guests (`/api/guests`) and groups (`/api/groups`),
  including a `bulk-import` endpoint that turns an uploaded Excel file
  into real Firestore records instead of a one-off in-memory run.
- Minimal CRUD for tables (`/api/tables`) — not in the original spec,
  but needed once `/optimize` can run without an Excel upload.
- `seating_optimizer.py` now supports **implicit group affinity**
  (+10.0 between same-group guests, unless an explicit pairwise
  affinity overrides it), a **group fragmentation penalty** (deducted
  per extra table a group gets split across), and a **capacity packing
  bonus** (rewarded when a table fills exactly).
- `/optimize` now runs two ways: with an uploaded Excel file (legacy,
  stateless), or with no file, pulling guests/groups/tables directly
  from Firestore and writing the resulting `table_id` back onto each
  guest.

**Frontend (`index.html`):**

- Rebuilt as a single-page app with a sidebar and three views: **Mesas
  & Optimizador** (the seating board), **Administrar Invitados** (a
  sortable, bulk-editable guest table), and **Grupos** (group cards with
  inline member management).
- Guest table supports 3-state column sorting (asc → desc → reset to
  ID) and a floating bulk toolbar for group/table (re)assignment and
  deletion.
- Board view supports per-table multi-select ("pencil" mode) so several
  guests can be dragged to another table in one move, with group color
  dots shown per guest.
- Custom visual identity (ink/wine/gold palette, Fraunces + Inter
  type) instead of default Tailwind styling.

**Bug found and fixed the same session:** "Guardar Distribución" wrote
to `arrangements/current`, but the board actually renders from each
guest's `table_id` field — and nothing read the snapshot back into the
guest records. So a save "succeeded" but didn't survive a refresh.
Fixed by having the save action `PUT` the moved guests' `table_id`
directly (tracked via a small dirty-set on drag), with the
`arrangements/current` snapshot kept as a secondary, human-readable
record.

**Reviewed, left unchanged:** `tables.py`. `capacity` is set correctly
per table, but `RectangularTable` and `ImperialTable` always generate a
fixed seat count (8 and 20) regardless of the `capacity` passed in —
only `CircularTable`'s geometry scales with capacity. Harmless today
since nothing reads `mesa.seats` yet; worth revisiting if a seat-level
(rather than table-level) layout is ever built.

## Architecture decisions

**Backend-mediated Firestore access, not client-side.**
The browser never talks to Firebase directly. It only calls our own API
endpoints, and the FastAPI backend is the only thing holding Firebase
credentials. Simpler than managing Firestore security rules for a
personal project, and keeps credentials off the client entirely.

**Guests are the source of truth for table assignment, not a separate
arrangement document.** Every view (board, guest list, groups) renders
from each guest's own `table_id` field. `arrangements/current` is kept
as a snapshot for convenience, but it's not what the app reads from —
this is what Session 2's bug fix depended on getting right.

**`table_id` on a guest stores the table's `name`, not its Firestore
document ID.** Matches how the optimizer keys assignments internally.
Anything touching guest–table relationships needs to stay consistent
with this.

**Dual credential loading: local file vs. environment variable.**
`main.py` checks for a `FIREBASE_SERVICE_ACCOUNT_JSON` environment
variable first, and falls back to a local `serviceAccountKey.json` file
if that's not set. Local development and Render deployment need
different approaches — Render builds from the GitHub repo, and the
credentials file is (correctly) never committed to that repo, so it has
to reach the server another way. The environment variable is set
directly in Render's dashboard and never touches git.

**Single Firestore document for the arrangement snapshot, not a
database schema.** Since this is a one-wedding-at-a-time personal tool,
`arrangements/current` stays a single document rather than a collection
per event. Multi-event support would mean adding a document per event,
but that's not needed until there's an actual second event to plan.

## Next step

Fix `/optimize`'s request signature: it currently mixes an
`Optional[UploadFile] = File(...)` parameter with an `OptimizeSettings`
Pydantic model as an implicit body, which FastAPI can't cleanly parse
together (a `File()` parameter forces multipart parsing, and the
Pydantic default won't arrive as JSON alongside it). The frontend works
around this today by always calling `/optimize` with no body at all, so
`group_weight`, `fragmentation_penalty`, `capacity_bonus`, and
`iterations` are stuck at their defaults and can't actually be tuned
from the UI. Splitting this into two routes (or moving the settings
onto `Form()` fields) would unblock exposing those knobs in the
Optimizador view.

## Files' Location

```
git ls-files
.gitignore
Algoritmo/index.html
Algoritmo/main.py
Algoritmo/plantilla_invitados.xlsx
Algoritmo/seating_optimizer.py
Algoritmo/tables.py
requirements.txt
```