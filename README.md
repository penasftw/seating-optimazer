# Seating Optimizer

A small tool that assigns wedding guests to tables using simulated annealing,
based on affinity scores between guests. Started as a favor for my brother's
wedding; currently a personal project, not a commercial product.

**Live app:** https://seating-optimazer.onrender.com

## How it works

1. Upload an Excel file with three sheets: `Invitados` (guest names),
   `Afinidades` (pairwise affinity scores between guests), and `Mesas`
   (table names, capacities, and types — circular, rectangular, or imperial).
2. The backend runs simulated annealing to find a table assignment that
   maximizes total affinity within each table.
3. The result renders as draggable cards in the browser, so you can
   manually fine-tune the algorithm's suggestion.
4. Manual changes can be saved and reloaded later.

## Stack

- **Backend:** FastAPI (Python), deployed on Render
- **Frontend:** Vanilla HTML/JS + Tailwind (via CDN), served directly by FastAPI
- **Persistence:** Firebase Firestore
- **Optimization:** Simulated annealing over guest-to-table assignments,
  scored by pairwise affinity

## What we built today

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

## Architecture decisions

**Backend-mediated Firestore access, not client-side.**
The browser never talks to Firebase directly. It only calls our own
`/save-arrangement` and `/load-arrangement` endpoints, and the FastAPI
backend is the only thing holding Firebase credentials. Simpler than
managing Firestore security rules for a personal project, and keeps
credentials off the client entirely.

**Single Firestore document, not a database schema.**
Since this is a one-wedding-at-a-time personal tool, all state lives in
one document (`arrangements/current`) rather than a collection per event
or per user. Deliberately the simplest thing that works — multi-event
support would mean adding a document per event, but that's not needed
until there's an actual second event to plan.

**Dual credential loading: local file vs. environment variable.**
`main.py` checks for a `FIREBASE_SERVICE_ACCOUNT_JSON` environment
variable first, and falls back to a local `serviceAccountKey.json` file
if that's not set. This exists because local development and Render
deployment need different approaches — Render builds from the GitHub
repo, and the credentials file is (correctly) never committed to that
repo, so it has to reach the server another way. The environment
variable is set directly in Render's dashboard and never touches git.

**Files' Location.** 
PS C:\Users\Abraham\Desktop\Personal\Proyectos Personales\Seating optimazer> git ls-files
.gitignore
Algoritmo/index.html
Algoritmo/main.py
Algoritmo/plantilla_invitados.xlsx
Algoritmo/seating_optimizer.py
Algoritmo/tables.py
requirements.txt


