---
title: Face Attendance Pro
emoji: 📷
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Face Attendance Pro

Face-recognition attendance with logins, an admin page, entry and exit times, a student limit,
live recognition and a leave system. The camera runs in the browser, so the same app works on your
computer and online.

## Run it on your computer (Windows PowerShell)

```powershell
py -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. The first time, the page asks you to create the **admin** account.
Mac or Linux: `python3 -m venv venv`, then `venv/bin/pip install -r requirements.txt` and `venv/bin/python app.py`.

Use the standard Python from python.org (3.10 to 3.13 is safest). OpenCV 5 is not supported yet,
so `requirements.txt` keeps OpenCV below version 5.

## Who can do what

The admin adds people on **Users & access**. Every person gets a **User ID and a password**. The password is
shown once when you add the person or reset it (it is stored scrambled and cannot be read back). For each
person the admin ticks which features they may use:

| Feature | What it allows |
|---|---|
| Live camera | Run the attendance camera for everyone |
| Own check-in | A student marks their own entry or exit with their own face only |
| Own attendance | A student sees their percentage and history |
| Apply for leave | A student sends leave requests |
| All reports | See everyone's attendance, download CSV |
| Edit attendance | Add or correct records by hand |
| Approve leave | Approve or reject leave requests |
| Manage students | Add, edit, delete students and enrol their faces |
| Settings | Change the rules, holidays and student limit |

Presets: **Admin** (everything), **Teacher** (live camera, reports, edit attendance, approve leave),
**Student** (own attendance, apply for leave). Any of them can be changed per person.

## How the features work

- **Entry and exit time.** The first recognition of the day is the entry. In Auto mode a later sighting at least
  the set number of minutes after entry (Settings, default 30) updates the exit time. The live camera can also
  be set to Entry only or Exit only. Time spent is shown in reports.
- **Student limit.** The admin sets a maximum number of students in Settings. Adding more is blocked. Only active,
  enrolled students are recognised. A face must be seen on two pictures in a row before attendance is marked.
- **Real-time recognition.** The live camera shows a box and name on each face and marks attendance on the spot.
- **Leave.** Approved leave days are taken out of both the days attended and the days counted, so during leave the
  percentage stays exactly where it was: it does not go down and it does not go up. Pending and rejected requests
  change nothing. Absent means a working day with no attendance and no approved leave.

## Put it online (Render, free plan)

1. Put these files in a GitHub repository (`Dockerfile` at the top level).
2. On render.com choose **New + > Web Service** (or **Blueprint** to use `render.yaml`), language **Docker**, **Free**.
3. Add these environment variables:
   - `SECRET_KEY`: any long random text (keeps people signed in).
   - `COOKIE_SECURE` = `1`
   - `ADMIN_PASSWORD`: creates the first admin (User ID `admin`) so nobody else can claim it.
   - the three Upstash settings below, so your data survives restarts.

## Keep your data when the free host restarts: Upstash (free)

Free hosts erase their disk when the app restarts or sleeps. That would delete every student, photo and record.
With an Upstash Redis database the app keeps an **encrypted copy** and refills itself from it.

1. Create a free database at console.upstash.com (type **Redis**, plan **Free**). In its **REST API** section copy
   `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` (the normal token, not read-only).
2. Pick a long secret phrase for `BACKUP_KEY` and **write it down somewhere safe**. The copy is encrypted with it.
   If the key is lost, the copy cannot be opened.
3. Add the three values as environment variables on the server (Render: **Environment** tab).
4. Open **Settings > Cloud copy**. It should say **On**. Press **Save now** once to check.

How it behaves:
- Changes are saved about a minute after the last one (at most five minutes while things keep changing). Photos are only
  uploaded again when they change. Attendance and settings are compressed first, so even years of records stay small.
- Everything is compressed and encrypted before it leaves the server. Upstash only ever holds unreadable text.
- If the server starts with an empty disk, it restores everything from the cloud copy **before anybody can use it**.
  If the cloud cannot be reached at that moment, first-time setup is blocked and the app keeps retrying, so a new admin
  can never overwrite your saved data. A wrong `BACKUP_KEY` also stops it instead of overwriting.
- Without these settings the app works as before and keeps data only on its own disk.
- Running the app on your own computer with the same Upstash settings would overwrite the online copy. For testing
  locally, leave the settings out or set a different `CLOUD_NAMESPACE`.
- Upstash free plan: 256 MB and 500,000 commands a month. This app uses a few commands per save.
- The **Download backup** button in Settings is still there as a second safety net.

## On a phone

- **Install it like an app (free, works now).** Open your address in the phone's browser. Android Chrome: tap **Install** on the
  banner, or the menu then **Install app**. iPhone Safari: tap **Share** then **Add to Home Screen**. It then opens full screen
  with its own icon. The live camera has a **Flip camera** button for the back camera.
- **Real Android app file (optional).** Go to pwabuilder.com, enter your address, choose **Package for Android**, and download the
  package. Then set `ANDROID_PACKAGE` (the package name you chose) and `ANDROID_SHA256` (the fingerprint PWABuilder shows you) on the
  server so the app opens without a browser address bar. The `.apk` can be installed directly; the Play Store needs a
  one-time 25 USD developer fee.
- **iPhone app store.** Apple needs a paid developer account (99 USD a year) and often rejects apps that only wrap a website.
  Adding the site to the Home Screen is the practical choice.

## Files

`app.py` the server, `templates/index.html` and `static/` the screens, `data/` the database (created on first run).
