# Rain Days Feature — Agent Briefing Document

## Overview

Add a new "Rain Days" module to the GUI that allows the user to retrieve daily rainfall data from the Oklahoma Mesonet, automatically identify days where rainfall exceeded 0.5 inches, and generate a separate inspection PDF for each qualifying rain day. The PDFs inherit all existing form data from the main inspection form — the only field that changes is "Type of Inspection," which gets prepended with "Rain Event."

---

## Phase 1 — Data Retrieval from Oklahoma Mesonet

### What is the Oklahoma Mesonet?

The Oklahoma Mesonet is a network of ~121 automated weather monitoring stations across Oklahoma (at least one per county). It provides daily summary data as comma-delimited CSV files. The relevant tool is their **Daily Data Retrieval** page, which accepts a date range, station selection, and variable selection, and returns rainfall data in CSV format.

- **Website:** https://www.mesonet.org
- **Daily Data Retrieval page (legacy URL still functional):** https://www.mesonet.org/index.php/past_data/daily_data_retrieval
- **Data runs midnight to midnight Central Standard Time (CST)**
- **Data is available back to January 1, 1994 for most stations**
- **Missing data is indicated by values less than -990 — these must be skipped, not treated as zero**
- **Data fees are waived for users inside Oklahoma**

### GUI Layout for the Rain Days Section

The section should be titled **"Rain Days"** (or similar). There is no need for a variable selection button — rainfall is the only variable we care about and should be pre-selected behind the scenes in the request.

**User-facing inputs:**

1. **Start Date** — month, day, year selectors (mirroring Mesonet's dropdowns)
2. **End Date** — month, day, year selectors (mirroring Mesonet's dropdowns)
3. **Station** — single-select dropdown populated with all Mesonet station codes and names (e.g., "BLAC - Blackwell", "CHIC - Chickasha", "NRMN - Norman"). This is a fixed/known list of ~121 stations. It can be hardcoded or stored in a config file. The full station list is available on the Mesonet daily data retrieval page.
4. **Email Address** — text input (optional, used only as fallback — see below)
5. **Submit Button**

### How the Data Request Works

The Mesonet daily data retrieval page is a standard HTML form that submits via HTTP POST. The app will replicate that POST request directly — no browser opens.

**Critical threshold:** Mesonet returns small requests (100 lines or fewer) as an instant HTTP response. Requests over 100 lines require an email address and the data is sent via email as a ZIP/CSV download.

Since we are requesting **one station** and **one variable** (rainfall), each day = one line. Therefore:

- **100 days or fewer = instant response, no email needed**
- **Over 100 days = would normally require email**

### Smart Chunking Algorithm

To avoid the email path entirely in most cases, the app should implement automatic chunking:

1. When the user clicks Submit, calculate the total number of days in the date range.
2. **If 100 days or fewer:** Send a single POST request to Mesonet's backend. Parse the CSV response directly. No email needed.
3. **If over 100 days:** Automatically split the date range into sequential chunks of **99 days each** (the last chunk will be whatever days remain). Fire each chunk as a separate POST request. Collect all the instant CSV responses and stitch them together into one combined dataset.
4. The user sees a progress indicator or confirmation in the GUI. No browser opens. No email required.

### Drag-and-Drop Fallback

Even though the chunking algorithm should handle most cases automatically, **keep a drag-and-drop zone in the GUI** as a manual fallback. This covers scenarios where:

- Mesonet changes their backend or form structure
- A request fails for any reason
- The user prefers to manually download from Mesonet

If the user drops a CSV file into this zone, the app parses it and proceeds to Phase 2 the same way it would with auto-retrieved data.

### Reverse-Engineering the Mesonet Form (One-Time Setup Task)

To send the POST request directly, the agent needs to inspect the Mesonet daily data retrieval page and capture:

- The form's **action URL** (the endpoint the form POSTs to)
- The **input field names** for: start month, start day, start year, end month, end day, end year, station code, selected variables, and email
- The **value** that corresponds to the daily rainfall variable

This is a one-time task. Once captured, these values can be hardcoded into the app's request logic. They should be documented in a config or constants file so they're easy to update if Mesonet ever changes them.

---

## Phase 2 — Filtering and PDF Generation

### Filtering Logic

Once the app has the rainfall CSV data (from auto-chunking or manual drag-and-drop):

1. Parse the CSV data.
2. Skip any rows where the rainfall value is less than -990 (this indicates missing data).
3. Identify every day where **daily rainfall > 0.5 inches**.
4. Each qualifying day becomes a rain day that needs its own PDF.

### PDF Generation

For each qualifying rain day, generate a PDF using the **existing inspection form data** that the user already filled out in the main form. All fields carry over as-is — project name, site address, inspector name, etc.

**The one field that changes:**

- **"Type of Inspection"** = `"Rain Event"` + the value the user typed into the main form's Type of Inspection field
  - Example: If the user entered "Weekly Walkthrough" in the main form, the rain day PDF's Type of Inspection reads: **"Rain Event - Weekly Walkthrough"** (or however the concatenation is formatted in the existing codebase)

The PDF template and generation logic should use the same pipeline as the existing inspection PDF system. No new form fields or user input needed for rain day PDFs.

### Output

- One PDF per qualifying rain day
- Each PDF should include/reflect the date of the specific rain event
- The rainfall amount for that day should be included on the PDF if the template supports it

---

## Summary of User Experience

1. User fills out the main inspection form as usual.
2. User scrolls to the **Rain Days** section.
3. User selects a start date, end date, and Mesonet station from the dropdowns.
4. User clicks Submit.
5. The app silently requests the data from Mesonet (chunking if necessary), filters for days over 0.5 inches, and generates a rain day PDF for each qualifying day — all using the data already in the main form.
6. If auto-retrieval fails, the user can manually download from Mesonet and drag-and-drop the CSV into the fallback zone.

---

## Key Technical Notes

- The Mesonet station list is a fixed set (~121 stations). Each has a 4-character code and a city name. This list should be stored in a config file or hardcoded constant.
- Rainfall data is measured in inches.
- CSV missing data sentinel: values < -990 mean no data, not zero rainfall.
- The 100-line instant-response threshold is based on total lines in the response (1 station × 1 variable × N days = N lines).
- Chunking at 99 days keeps each request safely under the 100-line email threshold.
- All Mesonet data is copyrighted. No redistribution is allowed per their terms of use. The app should use the data only for generating the user's own inspection documents.
