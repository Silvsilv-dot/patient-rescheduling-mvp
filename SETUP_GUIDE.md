# HealthFlow Clinic Showcase — Non-Coder Setup Guide (GitHub + Neon + Vercel)

This guide walks you step-by-step through uploading this build to a new GitHub branch, initializing your free Neon PostgreSQL database (with 6 months of historical records + 14 days of future slots), and deploying on Vercel—**without needing an IDE or local terminal**.

---

## Step 1: Create a New Neon PostgreSQL Database (Free Tier)

1. Go to [neon.tech](https://neon.tech) and sign up for a new free account.
2. Click **Create Project**, name it `healthflow-demo`, choose **US East (N. Virginia)** (closest to EST), and click **Create**.
3. Copy your **Pooled Connection String** (it looks like `postgresql://neondb_owner:...@ep-...-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require`) and save it in a temporary note.
4. In the left sidebar of Neon, click **SQL Editor**:
   - Open `db/schema.sql` from this unzipped folder in Notepad/TextEdit, copy all text, paste into the Neon SQL Editor, and click **Run**.
   - Next, open `db/seed_6month_neon.sql`, copy all text, paste into the Neon SQL Editor, and click **Run**.
   - Verify you see `Success`—this loads 40 synthetic patients, 6 clinicians, 3 clinic locations, 6 months of historical outcomes, and 14 days of upcoming open slots.

---

## Step 2: Upload These Files to a New Branch in GitHub (Browser Only)

1. Open your `patient-rescheduling-mvp` repository on GitHub.
2. Click the branch dropdown at the top-left (currently `main`), type `demo-showcase`, and click **Create branch: demo-showcase from main**.
3. Make sure the branch dropdown now shows **`demo-showcase`**.
4. Click **Add file → Upload files**.
5. Drag and drop **all files and folders** from inside the unzipped `patient-rescheduling-mvp-showcase` folder (`api/`, `db/`, `rules/`, `static/`, `vercel.json`, `requirements.txt`, `.env.example`, `SETUP_GUIDE.md`).
6. Scroll down, enter commit message `Add 4-role bilingual clinical showcase MVP`, and click **Commit changes**.

---

## Step 3: Deploy the Branch on Vercel (Free Tier)

1. Go to [vercel.com](https://vercel.com) and sign up with your GitHub account.
2. Click **Add New… → Project**, find `patient-rescheduling-mvp`, and click **Import**.
3. Open **Environment Variables** and add these two variables:
   - `NEON_DATABASE_URL` = *(paste your Neon pooled connection string from Step 1)*
   - `TOKEN_SIGNING_KEY` = *(type any random 32+ character string, e.g. `HealthFlowDemoShowcase2026SecureKey99`)*
4. Click **Deploy** and wait ~60 seconds.
5. To deploy directly from your `demo-showcase` branch (or inspect its preview URL):
   - In Vercel, open **Deployments** and click the preview deployment for branch **`demo-showcase`** (or go to **Settings → Git → Production Branch**, change `main` to `demo-showcase`, and click **Redeploy**).

---

## Step 4: Live Demo Walkthrough Checklist

When you open your Vercel URL (`https://your-project.vercel.app/`):
- **Starts on View 1 (Patient Rescheduling):** Use the top **One-Click Persona Switcher** to test:
  - *Patient #001 (2 Active Visits)* → Confirm a visit, compare wait times, and move a visit in plain English.
  - *Patient #002 (2/2 Reschedules Used)* → Shows the `BR-08` max-reschedule guardrail.
  - *Patient #003 (Visit < 24h)* → Shows the `BR-07` 24-hour lock window and late-cancellation notice.
- **Top Bar Controls:** Test the **911 Emergency Banner**, **EN | ES** Spanish toggle, **Dark / Light** theme toggle, **My Local Time vs. Clinic Base (EST/EDT)** switcher, and **Privacy & Consent** modal.
- **View 2 (Front-Desk Triage):** Drag any appointment row onto an open slot box (or click **Reschedule** / **Check In**) and watch Neon Postgres update immediately.
- **View 3 (Clinician Day View):** Broadcast a `+20m Delay` for Dr. M. Patel and switch back to Patient/Staff views to see the live delay warning appear.
- **View 4 (Manager Analytics 6-Mo):** Switch between **7 Days**, **30 Days**, **6 Months (Full History)**, and **Next 14 Days** to review utilization flags (`<60%` / `>90%`), late-cancellation rates, and the live **Immutable Audit Trail**.
