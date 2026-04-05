# TestLookup — GCP Deployment Guide
### For Beginners | 2-Developer Team | Lowest Cost (~$2–3/month)

> This document is the **VM-based deployment runbook**. For Kubernetes/OpenShift and multi-cloud strategies, also review `deployment_and_testing_strategy.md`, `installation.md`, and `docs/cloud-run-cloud-sql.md`.

---

## Table of Contents

1. [Cost Summary](#1-cost-summary)
2. [What You Need Before Starting](#2-what-you-need-before-starting)
3. [Architecture Overview (What Gets Deployed)](#3-architecture-overview)
4. [One-Time Setup — Google Cloud Project](#4-one-time-setup--google-cloud-project)
5. [One-Time Setup — Create Your VM](#5-one-time-setup--create-your-vm)
6. [One-Time Setup — Install Docker on the VM](#6-one-time-setup--install-docker-on-the-vm)
7. [One-Time Setup — Deploy the Application](#7-one-time-setup--deploy-the-application)
8. [One-Time Setup — Get a Free Gemini API Key](#8-one-time-setup--get-a-free-gemini-api-key)
9. [One-Time Setup — Configure and Start the App](#9-one-time-setup--configure-and-start-the-app)
10. [Daily Workflow — Start and Stop (Most Important!)](#10-daily-workflow--start-and-stop)
11. [How Both Developers Access the App](#11-how-both-developers-access-the-app)
12. [Optional — Auto Start/Stop with Cloud Scheduler](#12-optional--auto-startstop-with-cloud-scheduler)
13. [Troubleshooting Common Problems](#13-troubleshooting-common-problems)
14. [Cost Control Checklist](#14-cost-control-checklist)
15. [Updating the Application](#15-updating-the-application)

---

## 1. Cost Summary

> **Expected total cost: $2–3 per month** for 1 hour/day usage by 2 developers.

| Resource | Type | Cost/hour | Cost for 30 hrs/month |
|----------|------|-----------|----------------------|
| VM (e2-medium) | Compute | ~$0.034 | ~$1.01 |
| Disk (30 GB standard) | Storage | Always charged | ~$1.20 |
| Network egress | Traffic | ~$0.01/GB | Negligible for 2 devs |
| Gemini AI API | AI/LLM | **Free tier** | $0.00 |
| **Total** | | | **~$2.21/month** |

**Key rule: Stop the VM when you are done. The disk keeps your data safe while the VM is stopped. You only pay ~$0.04/hour while it is running.**

> The VM IP address will **change each time** you start it (this is normal and free). We will show you how to find the new IP each session.

---

## 2. What You Need Before Starting

Before you begin, make sure you have:

- [ ] A **Google account** (Gmail or Workspace)
- [ ] A **credit/debit card** linked to Google Cloud (required even for free-tier usage). You will not be charged much — the monthly total is $2–3.
- [ ] Access to this **GitHub repository** (or the code on your computer)
- [ ] A **web browser** (Chrome recommended)
- [ ] Basic familiarity with copy-pasting commands

**You do NOT need:**
- Prior cloud experience
- A domain name
- Any paid LLM API key (Gemini free tier is sufficient)

---

## 3. Architecture Overview

Here is what gets deployed on a single Google Cloud VM:

```
Your Browser / Developer Machine
          |
          | (HTTP over the internet)
          v
  ┌─────────────────────────────────────┐
  │     Google Cloud VM (e2-medium)     │
  │  ┌─────────┐   ┌──────────────────┐ │
  │  │Frontend │   │  Backend API     │ │
  │  │React    │   │  FastAPI :8000   │ │
  │  │Port 80  │   └──────────────────┘ │
  │  └─────────┘           |            │
  │                ┌───────┴────────┐   │
  │           ┌────┴──┐ ┌────────┐  │   │
  │           │Postgres│ │MongoDB │  │   │
  │           └───────┘ └────────┘  │   │
  │           ┌────────┐ ┌───────┐  │   │
  │           │ Redis  │ │ MinIO │  │   │
  │           └────────┘ └───────┘  │   │
  │           ┌───────────────┐      │   │
  │           │ Celery Worker │      │   │
  │           │ Celery Beat   │      │   │
  │           └───────────────┘      │   │
  │           ┌───────────────┐      │   │
  │           │ MCP Server    │      │   │
  │           │ Port 8002     │      │   │
  │           └───────────────┘      │   │
  └─────────────────────────────────────┘
          |
          | (API calls)
          v
  Google Gemini API (FREE tier — cloud AI)
```

**Services running inside the VM:**
- **Frontend** — the React dashboard (port 80)
- **Backend** — the FastAPI REST API (port 8000)
- **PostgreSQL** — stores test run data, projects, metrics
- **MongoDB** — stores raw logs and stack traces
- **Redis** — message broker for background jobs
- **MinIO** — S3-compatible file storage for test artifacts
- **Celery Worker + Beat** — processes AI analysis and scheduled jobs
- **MCP Server** — optional SSE endpoint for AI assistant integration

**NOT running on VM (to save resources and cost):**
- Ollama (local AI) — replaced by free Gemini API
- ChromaDB — disabled for basic dev/test usage

---

## 4. One-Time Setup — Google Cloud Project

> This section is done **once** by one developer. The other developer will share access.

### Step 4.1 — Open Google Cloud Console

1. Open your browser and go to: **https://console.cloud.google.com**
2. Sign in with your Google account
3. If prompted, agree to the Terms of Service

### Step 4.2 — Enable Billing

1. In the top menu, click the **Navigation Menu** (three horizontal lines ☰) on the top left
2. Click **"Billing"**
3. Click **"Link a billing account"** or **"Create billing account"**
4. Follow the prompts to add your credit card
5. You will NOT be charged unless you exceed the free tier or keep the VM running 24/7

### Step 4.3 — Set a Budget Alert (Important — Do This!)

This prevents accidental overspending:

1. In the Navigation Menu, click **"Billing"** → **"Budgets & alerts"**
2. Click **"+ Create Budget"**
3. Set:
   - **Name:** `testlookup-budget`
   - **Budget amount:** $10 (monthly)
   - **Alert thresholds:** 50%, 90%, 100%
   - **Email:** your email address
4. Click **"Save"**

> If you ever get a 90% alert email, log in and make sure the VM is stopped.

### Step 4.4 — Create a New Project

1. At the top of the page, click the **project dropdown** (it may say "My First Project" or similar)
2. Click **"New Project"**
3. Fill in:
   - **Project name:** `testlookup-dev`
   - **Project ID:** Leave as auto-generated (e.g., `testlookup-dev-123456`)
4. Click **"Create"**
5. Wait for the project to be created (about 10 seconds)
6. Make sure the new project is selected in the dropdown at the top

### Step 4.5 — Open Cloud Shell

Cloud Shell is a free terminal built into Google Cloud. You will use it to run all commands.

1. In the top-right corner of the Google Cloud Console, click the **Cloud Shell icon** (looks like `>_`)
2. A terminal panel will open at the bottom of the screen
3. Wait for it to say `your-username@cloudshell`

> All commands in this guide are typed into this Cloud Shell terminal.

### Step 4.6 — Enable Required APIs

In Cloud Shell, paste and run this command (press Enter after pasting):

```bash
gcloud services enable compute.googleapis.com
```

Wait until it says "Operation finished successfully." (takes about 30 seconds).

---

## 5. One-Time Setup — Create Your VM

### Step 5.1 — Create the Virtual Machine

In Cloud Shell, run the following command. This creates your VM:

```bash
gcloud compute instances create testlookup-vm \
  --zone=us-central1-a \
  --machine-type=e2-medium \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-type=pd-standard \
  --boot-disk-size=30GB \
  --tags=testlookup-web
```

**What each option means (for your learning):**
- `--zone=us-central1-a` — US Central region (cheapest zone)
- `--machine-type=e2-medium` — 2 vCPUs, 4 GB RAM (enough for all services)
- `--image-family=debian-12` — Linux operating system (Debian, very stable)
- `--boot-disk-type=pd-standard` — Standard (cheaper) magnetic disk, not SSD
- `--boot-disk-size=30GB` — 30 GB storage for OS + app + databases
- `--tags=testlookup-web` — A label used to apply firewall rules

After running, you should see output like:
```
NAME           ZONE           MACHINE_TYPE  ...  STATUS
testlookup-vm   us-central1-a  e2-medium     ...  RUNNING
```

### Step 5.2 — Open Firewall Ports

This allows you and your teammate to reach the application from your browsers:

```bash
gcloud compute firewall-rules create testlookup-allow-web \
  --allow=tcp:22,tcp:80,tcp:8000,tcp:8002 \
  --target-tags=testlookup-web \
  --source-ranges=0.0.0.0/0 \
  --description="Allow SSH, frontend, and backend access"
```

**Ports explained:**
- `22` — SSH (secure remote access to the VM)
- `80` — Frontend web dashboard
- `8000` — Backend API and its documentation page
- `8002` — MCP SSE endpoint (optional; expose only if required)

---

## 6. One-Time Setup — Install Docker on the VM

### Step 6.1 — Connect to the VM

In Cloud Shell, run:

```bash
gcloud compute ssh testlookup-vm --zone=us-central1-a
```

If asked "Do you want to continue? (Y/n)", type `Y` and press Enter.

You are now **inside the VM**. Your terminal prompt will change to something like:
```
your-username@testlookup-vm:~$
```

> All commands from here until Section 9 are run **inside the VM**.

### Step 6.2 — Update the System

```bash
sudo apt-get update -y && sudo apt-get upgrade -y
```

This updates the operating system. It may take 1–2 minutes.

### Step 6.3 — Install Required Tools

```bash
sudo apt-get install -y ca-certificates curl gnupg git make
```

### Step 6.4 — Install Docker

Run these commands one at a time, in order:

```bash
# Step 1: Set up Docker's official package source
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
# Step 2: Install Docker
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

```bash
# Step 3: Allow your user to run Docker without sudo
sudo usermod -aG docker $USER
newgrp docker
```

### Step 6.5 — Verify Docker Is Working

```bash
docker --version
docker compose version
```

You should see output like:
```
Docker version 27.x.x, build ...
Docker Compose version v2.x.x
```

---

## 7. One-Time Setup — Deploy the Application

### Step 7.1 — Clone the Repository

> Still inside the VM. Replace the URL below with your actual GitHub repository URL.

```bash
cd ~
git clone https://github.com/YOUR_ORG/testlookup.git
cd testlookup
```

> If your repository is private, you will need to authenticate. Either:
> - Use a **GitHub Personal Access Token** as the password when prompted, OR
> - Set up SSH keys (`ssh-keygen` and add the public key to GitHub Settings → SSH Keys)

### Step 7.2 — Confirm the Files Are There

```bash
ls -la
```

You should see files like `docker-compose.yml`, `Makefile`, `.env.example`, etc.

---

## 8. One-Time Setup — Get a Free Gemini API Key

The application uses AI to analyze test failures. Instead of running a heavy local AI model (which needs more RAM and disk), we use Google's Gemini API which has a **generous free tier**.

**Free tier limits (as of 2025):**
- Gemini 1.5 Flash: 15 requests/minute, 1 million tokens/day — more than enough for dev/test

### Step 8.1 — Get the API Key

1. Open a **new browser tab** (keep the VM terminal open)
2. Go to: **https://aistudio.google.com/app/apikey**
3. Sign in with your Google account (same one used for GCP)
4. Click **"Create API key"**
5. Select your project (`testlookup-dev`) from the dropdown
6. Click **"Create API key in existing project"**
7. **Copy the key** (it looks like: `AIzaSy...`) and save it somewhere safe (e.g., a text file on your computer)

> Keep this key private. Do not share it publicly or commit it to GitHub.

---

## 9. One-Time Setup — Configure and Start the App

### Step 9.1 — Create the Environment File

Go back to the VM terminal and run:

```bash
cd ~/testlookup
cp .env.gcp-vm.example .env
```

### Step 9.2 — Get the VM's Public IP Address

```bash
curl -s ifconfig.me
```

This prints your VM's public IP. **Write it down** — you will need it in the next step.

Example output: `34.123.45.67`

### Step 9.3 — Generate Secure Secret Keys

Run these two commands. Each generates a random secret key:

```bash
echo "APP_SECRET_KEY: $(openssl rand -hex 32)"
echo "JWT_SECRET_KEY: $(openssl rand -hex 32)"
```

**Write down both output values.** You will paste them into the config file next.

Example output:
```
APP_SECRET_KEY: a3f8c2e1d4b7...
JWT_SECRET_KEY: f9e2c1a4b8d3...
```

### Step 9.4 — Edit the Configuration File

Open the configuration file in the built-in text editor:

```bash
nano .env
```

The file will open in the terminal. Use the **arrow keys** to navigate to each line that needs changing. Edit these values:

| Find this line | Replace with |
|----------------|--------------|
| `APP_SECRET_KEY=replace-with-strong-random-value` | `APP_SECRET_KEY=<your generated key from Step 9.3>` |
| `CORS_ORIGINS=http://localhost,http://YOUR_VM_PUBLIC_IP` | `CORS_ORIGINS=http://localhost,http://<your VM IP>` |
| `POSTGRES_PASSWORD=replace-with-strong-db-password` | `POSTGRES_PASSWORD=MySecureDBPass2024!` |
| `MINIO_ACCESS_KEY=replace-with-minio-user` | `MINIO_ACCESS_KEY=minioadmin` |
| `MINIO_SECRET_KEY=replace-with-minio-password` | `MINIO_SECRET_KEY=MyMinioPass2024!` |
| `VITE_API_BASE_URL=http://YOUR_VM_PUBLIC_IP:8000` | `VITE_API_BASE_URL=http://<your VM IP>:8000` |
| `LLM_PROVIDER=gemini` | Leave as is |
| `LLM_MODEL=gemini-1.5-flash` | Leave as is |
| `GOOGLE_API_KEY=replace-with-gemini-api-key` | `GOOGLE_API_KEY=<your Gemini key from Step 8>` |
| `JWT_SECRET_KEY=replace-with-strong-jwt-secret` | `JWT_SECRET_KEY=<your generated key from Step 9.3>` |

**To save and exit nano:**
1. Press `Ctrl + O` (save)
2. Press `Enter` (confirm filename)
3. Press `Ctrl + X` (exit)

### Step 9.5 — Verify the Configuration File

```bash
grep -E "APP_SECRET_KEY|GOOGLE_API_KEY|POSTGRES_PASSWORD|VITE_API_BASE_URL" .env
```

Make sure none of the lines still say `replace-with-...`. All four should show real values.

### Step 9.6 — Add Swap Memory (Prevents Out-of-Memory Crashes)

The VM has 4 GB RAM. Adding swap gives a safety buffer:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Step 9.7 — Start the Application

This command builds and starts all services. **It will take 5–10 minutes the first time** as it downloads and builds Docker images.

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build
```

Watch for any errors. If it completes without `ERROR` messages, you are good.

Check that all containers started:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml ps
```

You should see these containers with status `healthy` or `running`:

```
NAME                  STATUS
testlookup_postgres    running (healthy)
testlookup_mongo       running (healthy)
testlookup_redis       running (healthy)
testlookup_minio       running (healthy)
testlookup_backend     running (healthy)
testlookup_frontend    running
```

> It may take 2–3 minutes for `backend` to show as `healthy` after first start.

### Step 9.8 — Run Database Migrations

This creates the database tables. Run this **once**:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic upgrade head
```

You should see output ending with something like:
```
INFO  [alembic.runtime.migration] Running upgrade  -> abc123def456, initial schema
```

### Step 9.9 — Verify the Application Is Running

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "healthy"}
```

### Step 9.10 — Access the Application

Open your browser and navigate to:

- **API Docs:** `http://<your VM IP>:8000/docs`. Under the `Authentication` section, expand `POST /api/v1/auth/register`, click "Try it out", and create your first administrator/test user.
- **Dashboard:** `http://<your VM IP>` (e.g., `http://34.123.45.67`). You will be redirected to the Login page. Use the credentials you just created.

You should see the TestLookup dashboard.

### Step 9.11 — Also Start Background Workers (Optional but Recommended)

The Celery workers enable background AI analysis of test failures. Start them:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat
```

---

## 10. Daily Workflow — Start and Stop

> **This is the most important section for cost control.** Always stop the VM when done.

### Starting the Application Each Day

#### From Google Cloud Console (Web UI — Easiest)

1. Go to **https://console.cloud.google.com**
2. Navigate to **Compute Engine** → **VM instances**
3. Find `testlookup-vm`, click the **three dots (⋮)** on the right
4. Click **"Start / Resume"**
5. Wait about 60 seconds for it to start
6. Click on `testlookup-vm` to open its details
7. Find **"External IP"** — this is today's IP address (write it down)

#### From Cloud Shell (Terminal — Faster)

In Cloud Shell (not on the VM), run:

```bash
# Start the VM
gcloud compute instances start testlookup-vm --zone=us-central1-a

# Get today's IP address
gcloud compute instances describe testlookup-vm \
  --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

### Reconnecting to the Running Application

1. SSH into the VM: `gcloud compute ssh testlookup-vm --zone=us-central1-a`
2. Start Docker services (they should start automatically, but if not):

```bash
cd ~/testlookup
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat
```

3. Check services are running:
```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml ps
```

4. Open the dashboard at the new IP address:
```bash
# Get today's IP
curl -s ifconfig.me
```

### Stopping the Application Each Day

#### Step 1 — Stop Docker Containers First (Optional but Good Practice)

SSH into the VM and run:

```bash
cd ~/testlookup
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async down
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml down
```

Then exit the VM:

```bash
exit
```

#### Step 2 — Stop the VM (REQUIRED for cost savings)

From Cloud Shell (you are back in Cloud Shell after `exit`):

```bash
gcloud compute instances stop testlookup-vm --zone=us-central1-a
```

Or from the **Google Cloud Console**:
1. Go to **Compute Engine** → **VM instances**
2. Click the **three dots (⋮)** next to `testlookup-vm`
3. Click **"Stop"**
4. Confirm

> **Verify the VM is stopped:** The status should show as `TERMINATED` in the console. You stop paying for compute as soon as it reaches `TERMINATED`. Disk storage (~$1.20/month) continues but that is expected.

### Quick Daily Reference Card

```
MORNING (Start):
  1. Cloud Console → Compute Engine → Start VM
  2. Wait 60 sec → Note the new External IP
  3. SSH in: gcloud compute ssh testlookup-vm --zone=us-central1-a
  4. cd ~/testlookup
  5. docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d
  6. Open browser: http://<new IP>

EVENING (Stop):
  1. SSH into VM, run:
     docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml down
  2. exit
  3. gcloud compute instances stop testlookup-vm --zone=us-central1-a
  4. Verify status = TERMINATED in Cloud Console
```

---

## 11. How Both Developers Access the App

Only **one developer** needs to manage the VM (start/stop, deploy updates). The second developer only needs the current IP address.

### Giving the Second Developer Access

**Option A: Share the IP address** (simplest)
- Developer 1 starts the VM each morning and messages Developer 2 the IP
- Developer 2 opens `http://<IP>` in their browser

**Option B: Add Developer 2 as a GCP project member**
1. Developer 1 goes to: **https://console.cloud.google.com/iam-admin/iam**
2. Clicks **"+ Grant Access"**
3. Enters Developer 2's Google email address
4. Sets role to **"Compute Instance Admin (v1)"**
5. Clicks **"Save"**
6. Developer 2 can now also start/stop the VM from their own Cloud Console

### Avoiding Conflicts

- Only **one developer** should be running tests or uploading results at the same time
- Use a group chat or Slack channel to coordinate: "Starting VM now" / "Done, stopping VM"
- All data is stored in Docker volumes on the disk — it persists between VM starts

---

## 12. Optional — Auto Start/Stop with Cloud Scheduler

If you want the VM to start and stop automatically (e.g., 9 AM start, 6 PM stop), you can use **Cloud Scheduler**. This section is optional.

### Step 12.1 — Enable Required APIs

In Cloud Shell:

```bash
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable cloudfunctions.googleapis.com
```

### Step 12.2 — Create a Service Account for Scheduling

```bash
# Create service account
gcloud iam service-accounts create vm-scheduler \
  --display-name="VM Scheduler"

# Get your project ID
PROJECT_ID=$(gcloud config get-value project)

# Grant permission to start/stop VMs
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:vm-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1"
```

### Step 12.3 — Create Start Schedule (9 AM weekdays)

```bash
PROJECT_ID=$(gcloud config get-value project)

gcloud scheduler jobs create http testlookup-vm-start \
  --location=us-central1 \
  --schedule="0 9 * * 1-5" \
  --time-zone="America/Chicago" \
  --uri="https://compute.googleapis.com/compute/v1/projects/${PROJECT_ID}/zones/us-central1-a/instances/testlookup-vm/start" \
  --message-body="" \
  --oauth-service-account-email="vm-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" \
  --http-method=POST
```

> Change `America/Chicago` to your timezone. Common options: `America/New_York`, `America/Los_Angeles`, `Europe/London`, `Asia/Kolkata`

### Step 12.4 — Create Stop Schedule (6 PM weekdays)

```bash
PROJECT_ID=$(gcloud config get-value project)

gcloud scheduler jobs create http testlookup-vm-stop \
  --location=us-central1 \
  --schedule="0 18 * * 1-5" \
  --time-zone="America/Chicago" \
  --uri="https://compute.googleapis.com/compute/v1/projects/${PROJECT_ID}/zones/us-central1-a/instances/testlookup-vm/stop" \
  --message-body="" \
  --oauth-service-account-email="vm-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" \
  --http-method=POST
```

### Step 12.5 — Make Docker Services Start on VM Boot

SSH into the VM and run these two commands to make Docker auto-start the containers when the VM boots:

```bash
# Enable Docker to start on boot
sudo systemctl enable docker

# Create a startup script
cat << 'EOF' | sudo tee /etc/rc.local
#!/bin/bash
sleep 30
cd /home/$(ls /home | head -1)/testlookup
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat
exit 0
EOF
sudo chmod +x /etc/rc.local
```

> After this, when the VM starts automatically via scheduler, the app will also start automatically within ~30 seconds.

**Note on Cloud Scheduler cost:** Cloud Scheduler has a free tier of 3 jobs/month. Two jobs (start + stop) is within the free tier.

---

## 13. Troubleshooting Common Problems

### Problem: "Cannot connect to http://\<IP\>"

**Cause:** Services may not be running yet, or the IP changed.

**Fix:**
```bash
# SSH into VM
gcloud compute ssh testlookup-vm --zone=us-central1-a

# Check if containers are running
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml ps

# If containers are stopped, start them
cd ~/testlookup
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d

# Get the current IP (it may have changed since last start)
curl -s ifconfig.me
```

---

### Problem: "Backend is unhealthy" or "Service Unavailable"

**Fix:** Check the backend logs for errors:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml logs --tail=50 backend
```

Common causes:
- Database not ready yet → wait 30 seconds and retry
- Wrong environment variable → re-check `.env` file

---

### Problem: "Gemini API error" or "AI analysis not working"

**Fix:** Verify your API key is correct:

```bash
grep GOOGLE_API_KEY ~/testlookup/.env
```

Then test the key:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_GOOGLE_API_KEY"
```

If you get a list of models, the key is valid. If you get an error, get a new key from https://aistudio.google.com/app/apikey.

---

### Problem: VM runs out of memory (OOM)

**Symptom:** Containers keep restarting, system becomes very slow.

**Fix:**
```bash
# Check memory usage
free -h

# Check which containers are using the most memory
docker stats --no-stream

# Restart just the heavy containers
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml restart backend worker
```

If this happens frequently, the swap file may not be configured. Run:

```bash
sudo swapon --show
```

If empty, re-run Step 9.6 to add swap.

---

### Problem: "No space left on device"

**Fix:**
```bash
# Check disk usage
df -h

# Clean up unused Docker images and containers (safe to run)
docker system prune -f

# Check large Docker volumes
docker system df
```

---

### Problem: Database migration errors

```bash
# Check current migration version
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic current

# Re-run migrations
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic upgrade head
```

---

### Problem: Frontend shows "Cannot connect to backend"

**Cause:** The `VITE_API_BASE_URL` in `.env` still has the old IP (IPs change on each VM start).

**Fix — Rebuild the Frontend with the New IP:**

```bash
cd ~/testlookup

# Get today's IP
NEW_IP=$(curl -s ifconfig.me)

# Update the .env file
sed -i "s|VITE_API_BASE_URL=http://.*:8000|VITE_API_BASE_URL=http://${NEW_IP}:8000|g" .env

# Rebuild just the frontend
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build frontend
```

> **Long-term fix:** Use a static IP (adds ~$4/month) or configure a free dynamic DNS service like DuckDNS. Not necessary for 2-developer dev/test use.

---

### Problem: Forgot to stop the VM / worried about cost

Check VM status from any machine:

```bash
gcloud compute instances describe testlookup-vm \
  --zone=us-central1-a \
  --format='get(status)'
```

- `RUNNING` = you are being charged ~$0.034/hour → stop it if not needed
- `TERMINATED` = stopped, only disk storage charge applies

Stop it if running:

```bash
gcloud compute instances stop testlookup-vm --zone=us-central1-a
```

---

## 14. Cost Control Checklist

Use this checklist to keep costs under control:

**Daily:**
- [ ] Start the VM only when you plan to use it
- [ ] Stop the VM when done for the day
- [ ] Confirm the VM shows `TERMINATED` in the Cloud Console after stopping

**Weekly:**
- [ ] Check billing: **Billing** → **Reports** in Google Cloud Console
- [ ] Verify no unexpected services are running
- [ ] Clean Docker cache on VM to free disk space:
  ```bash
  docker system prune -f
  ```

**Monthly:**
- [ ] Review your billing statement (should be $2–4)
- [ ] Check if any budget alerts were triggered
- [ ] Rotate secrets if needed (update `.env` and restart containers)

**If you want to pause development for weeks/months:**
```bash
# Stop the VM (saves ~$1/month vs deleting disk)
gcloud compute instances stop testlookup-vm --zone=us-central1-a

# Or, if you are completely done and want to delete everything:
gcloud compute instances delete testlookup-vm --zone=us-central1-a
# WARNING: This deletes ALL data. Cannot be undone.
```

---

## 15. Updating the Application

When the code changes and you want to redeploy:

### Step 15.1 — SSH into the VM

```bash
gcloud compute ssh testlookup-vm --zone=us-central1-a
cd ~/testlookup
```

### Step 15.2 — Pull Latest Code

```bash
git pull origin main
```

### Step 15.3 — Rebuild and Restart

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build
```

### Step 15.4 — Run New Migrations (if any)

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic upgrade head
```

### Step 15.5 — Restart Workers

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async down
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat
```

---

## Appendix A — All Services and Their Ports

| Service | Port | Accessible From | Purpose |
|---------|------|----------------|---------|
| Frontend (React) | 80 | Internet browser | Main dashboard |
| Backend API (FastAPI) | 8000 | Internet browser | REST API + Swagger docs at /docs |
| PostgreSQL | 5432 | VM internal only | Structured test data |
| MongoDB | 27017 | VM internal only | Logs and stack traces |
| Redis | 6379 | VM internal only | Message broker |
| MinIO (S3) | 9000/9001 | VM internal only | File storage |

> Databases and storage services are **not exposed to the internet** for security. They are only reachable from within the VM.

---

## Appendix B — Important File Locations (on the VM)

| File | Location | Purpose |
|------|----------|---------|
| Environment config | `~/testlookup/.env` | All secrets and settings |
| Application code | `~/testlookup/` | Full project |
| Database data | Docker volume `postgres_data` | PostgreSQL data (persisted) |
| MongoDB data | Docker volume `mongo_data` | MongoDB data (persisted) |
| File storage | Docker volume `minio_data` | Test artifacts (persisted) |

Data in Docker volumes **persists when containers stop** and **persists when the VM is stopped**. It is only lost if you run `docker compose down -v` (which explicitly deletes volumes) or delete the VM disk.

---

## Appendix C — Monthly Cost Breakdown (Detailed)

| Item | Unit Price | Usage (30 hrs/month) | Monthly Cost |
|------|-----------|---------------------|-------------|
| e2-medium compute | $0.03351/hr | 30 hrs | $1.01 |
| pd-standard disk 30GB | $0.04/GB/month | Always-on | $1.20 |
| Network egress (Americas) | $0.01/GB | ~1GB (2 devs) | $0.01 |
| Cloud Scheduler (2 jobs) | Free tier | 2 jobs | $0.00 |
| Gemini 1.5 Flash API | Free tier | <1M tokens/day | $0.00 |
| Static IP (if added later) | $0.004/hr | Optional | Optional |
| **Total** | | | **~$2.22/month** |

> Prices are based on GCP us-central1 pricing as of early 2026. Verify current prices at https://cloud.google.com/compute/all-pricing

---

*Last updated: March 2026*
*Guide version: 1.0*
*Target audience: Beginner/novice GCP users*
