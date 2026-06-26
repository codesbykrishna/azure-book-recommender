# Shelf — Personalized Book Recommendation Engine
## Complete Deployment Guide

---

## Architecture Overview

```
data.csv
   │
   ▼
[preprocessing/enrich_data.py]  ──▶  bookstorage105 Blob
   │  (run once locally)              ├── enriched_data.json  (full data + key phrases)
   │                                  └── titles.json         (lightweight autocomplete list)
   │
   ▼
[backend/ — Azure Function App: book-recommend-api]
   ├── GET  /api/titles      ← returns titles list (fallback for frontend)
   ├── POST /api/recommend   ← fuzzy match + key-phrase similarity + Azure OpenAI explanation + Translator
   └── POST /api/chat        ← conversational chatbot (Azure OpenAI + Translator)
         ▲
         │  calls
         ├── booklanguage   (Azure AI Language — key phrases, done in preprocessing)
         ├── Azure OpenAI   (via Azure AI Foundry — natural language explanations + chat)
         └── translator-book (Azure Translator — multilingual output)

[frontend/ — Static Website]
   ├── index.html
   ├── style.css
   ├── app.js
   └── titles.json  ← copied here from preprocessing output
```

---

## Prerequisites — Install on Your Laptop

```bash
# 1. Python 3.10+
python --version

# 2. Azure Functions Core Tools v4
npm install -g azure-functions-core-tools@4 --unsafe-perm true

# 3. Azure CLI
# Windows: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows
# Mac:     brew install azure-cli
# Linux:   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

az login     # log in to your Azure account
```

---

## STEP 1 — Collect your Azure resource keys

Go to the Azure Portal and collect these values. Keep them handy.

### 1a. booklanguage (Azure AI Language)
- Portal → booklanguage → Keys and Endpoint
- Copy **Key 1** and **Endpoint**

### 1b. translator-book (Azure Translator)
- Portal → translator-book → Keys and Endpoint
- Copy **Key 1**, **Endpoint**, and **Location/Region**

### 1c. bookstorage105 (Storage Account — book data)
- Portal → bookstorage105 → Access keys → Show keys
- Copy **Connection string** for key1

### 1d. bookrecommendng9c2c (Storage Account — Function App runtime)
- Portal → bookrecommendng9c2c → Access keys → Show keys
- Copy **Connection string** for key1

### 1e. Azure OpenAI / Azure AI Foundry
- Go to Azure AI Foundry: https://ai.azure.com
- Create a project → Deploy a model → choose **gpt-4o-mini** (cheapest, fast)
- Once deployed, go to: Chat playground → View code → note:
  - **Endpoint** (e.g. https://YOUR-RESOURCE.openai.azure.com/)
  - **API Key**
  - **Deployment name** (e.g. gpt-4o-mini)

---

## STEP 2 — Run Preprocessing (one time only)

This reads data.csv, extracts key phrases via Azure AI Language, and
uploads enriched_data.json + titles.json to Blob Storage.

```bash
cd book-recommender/preprocessing

# Install dependencies
pip install -r requirements.txt

# Set environment variables (Windows PowerShell — replace values)
$env:LANGUAGE_ENDPOINT   = "https://<your-booklanguage>.cognitiveservices.azure.com/"
$env:LANGUAGE_KEY        = "<booklanguage key>"
$env:STORAGE_CONNECTION_STR = "<bookstorage105 connection string>"
$env:BLOB_CONTAINER      = "bookdata"
$env:DATA_CSV_PATH       = "data.csv"

# Set environment variables (Mac/Linux)
export LANGUAGE_ENDPOINT="https://<your-booklanguage>.cognitiveservices.azure.com/"
export LANGUAGE_KEY="<booklanguage key>"
export STORAGE_CONNECTION_STR="<bookstorage105 connection string>"
export BLOB_CONTAINER="bookdata"
export DATA_CSV_PATH="data.csv"

# Run the script
python enrich_data.py
```

This will take ~15–25 minutes for 12,800 books (batched API calls).
When done, you'll see:
  - enriched_data.json  uploaded to bookstorage105/bookdata/
  - titles.json         uploaded to bookstorage105/bookdata/

**Also copy titles.json into the frontend/ folder:**
```bash
cp titles.json ../frontend/titles.json
```

---

## STEP 3 — Configure the Azure Function App

### 3a. Set Application Settings in Azure Portal

Go to: Azure Portal → book-recommend-api → Configuration → Application settings

Click "+ New application setting" for each of these:

| Name                      | Value                                                        |
|---------------------------|--------------------------------------------------------------|
| STORAGE_CONNECTION_STR    | <bookstorage105 connection string>                           |
| BLOB_CONTAINER            | bookdata                                                     |
| AZURE_OPENAI_ENDPOINT     | https://<your-foundry-resource>.openai.azure.com/            |
| AZURE_OPENAI_KEY          | <Azure OpenAI key>                                           |
| AZURE_OPENAI_DEPLOYMENT   | gpt-4o-mini                                                  |
| AZURE_OPENAI_API_VERSION  | 2024-08-01-preview                                           |
| TRANSLATOR_KEY            | <translator-book key>                                        |
| TRANSLATOR_ENDPOINT       | https://api.cognitive.microsofttranslator.com                |
| TRANSLATOR_REGION         | <translator-book region, e.g. eastus>                        |

Click **Save** after adding all settings.

### 3b. Set local settings for testing (optional but recommended)

```bash
cd book-recommender/backend
cp local.settings.json.example local.settings.json
# Now open local.settings.json and fill in your actual keys
```

---

## STEP 4 — Test the backend locally (optional)

```bash
cd book-recommender/backend

pip install -r requirements.txt

func start
# The Functions runtime will start at http://localhost:7071

# Test /api/titles
curl http://localhost:7071/api/titles

# Test /api/recommend
curl -X POST http://localhost:7071/api/recommend \
  -H "Content-Type: application/json" \
  -d '{"book_title": "Harry Potter", "language": "en", "top_n": 3}'

# Test /api/chat
curl -X POST http://localhost:7071/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What should I read after Harry Potter?", "history": [], "language": "en"}'
```

---

## STEP 5 — Deploy the backend (Azure Functions)

```bash
cd book-recommender/backend

# Deploy to your existing Function App
func azure functionapp publish book-recommend-api --python

# Wait for deployment to finish, then test the live endpoint:
curl https://book-recommend-api.azurewebsites.net/api/titles
```

If you see a JSON array of titles, the backend is live. ✅

---

## STEP 6 — Configure the frontend

Open `book-recommender/frontend/app.js` and update line 14:

```js
// BEFORE
const API_BASE = "https://book-recommend-api.azurewebsites.net/api";

// AFTER — replace with your actual Function App URL
const API_BASE = "https://book-recommend-api.azurewebsites.net/api";
// (it may already be correct — double-check the URL in the Azure Portal
//  under: book-recommend-api → Overview → URL)
```

---

## STEP 7 — Deploy the frontend as a Static Website

We'll use `bookrecommendng9c2c` as the static website host.

### 7a. Enable Static Website hosting

```bash
az storage blob service-properties update \
  --connection-string "<bookrecommendng9c2c connection string>" \
  --static-website \
  --index-document index.html \
  --404-document index.html
```

### 7b. Upload the frontend files

```bash
az storage blob upload-batch \
  --connection-string "<bookrecommendng9c2c connection string>" \
  --source book-recommender/frontend/ \
  --destination '$web' \
  --overwrite
```

### 7c. Get the website URL

```bash
az storage account show \
  --name bookrecommendng9c2c \
  --query "primaryEndpoints.web" \
  --output tsv
```

You'll get a URL like: `https://bookrecommendng9c2c.z13.web.core.windows.net`
Open it in your browser — your app is live! 🎉

---

## STEP 8 — Enable CORS on the Function App

So the frontend can call the backend:

```bash
az functionapp cors add \
  --name book-recommend-api \
  --resource-group book-recommend-.ng \
  --allowed-origins "https://bookrecommendng9c2c.z13.web.core.windows.net"
```

Also add `http://localhost:5500` or `http://127.0.0.1:5500` if you want
to test the frontend locally with Live Server (VS Code extension).

---

## Folder Summary

```
book-recommender/
├── preprocessing/
│   ├── enrich_data.py       ← run once to generate enriched data
│   ├── requirements.txt
│   └── data.csv             ← your dataset (copy here)
│
├── backend/
│   ├── function_app.py      ← 3 API endpoints
│   ├── similarity.py        ← fuzzy match + scoring
│   ├── openai_helper.py     ← Azure OpenAI / Foundry
│   ├── translator_helper.py ← Azure Translator
│   ├── requirements.txt
│   ├── host.json
│   └── local.settings.json  ← your local keys (never commit this)
│
└── frontend/
    ├── index.html
    ├── style.css
    ├── app.js               ← update API_BASE on line 14
    └── titles.json          ← copy from preprocessing output
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `func start` fails | Make sure Azure Functions Core Tools v4 is installed: `func --version` |
| "enriched_data.json not found" error from Function | Verify preprocessing completed and check BLOB_CONTAINER setting |
| Blank autocomplete dropdown | Make sure titles.json is in the frontend/ folder OR the /api/titles endpoint works |
| CORS error in browser | Re-run Step 8 with your exact frontend URL |
| Azure OpenAI 404 | Double-check AZURE_OPENAI_DEPLOYMENT matches your deployment name exactly |
| Translator not working | Make sure TRANSLATOR_REGION is set (e.g. "eastus", not "East US") |

---

## Cost Estimate (approximate, free tier / minimal usage)

| Service | Cost |
|---|---|
| Azure AI Language (key phrases, one-time batch) | ~$0.50–$1.50 for 12,800 docs |
| Azure OpenAI gpt-4o-mini | ~$0.001 per recommendation explanation |
| Azure Translator | First 2M chars/month free |
| Azure Functions | First 1M executions/month free |
| Blob Storage | ~$0.02/GB/month |
| Static Website (Storage) | Minimal |
