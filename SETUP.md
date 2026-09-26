# Setup Guide

## 1. Python version

Use **Python 3.11** (the deployment target). Newer versions (e.g. 3.13/3.14) may lack prebuilt wheels for `torch` / `sentence-transformers` / `chromadb`. Check with `python --version`; on Windows, `py -3.11 --version` shows if 3.11 is installed (get it from python.org if not).

## 2. Create and activate a virtual environment

Windows (PowerShell):
```powershell
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
```
Windows (cmd): `venv\Scripts\activate.bat`

macOS / Linux:
```bash
python3.11 -m venv venv
source venv/bin/activate
```

## 3. Install dependencies

```
python -m pip install --upgrade pip
pip install -r requirements.txt
```
The first install is large (PyTorch); the embedding model downloads on first use.

## 4. Configure environment

```
copy .env.example .env      # Windows   (cp on macOS/Linux)
```
Edit `.env` and set `GEMINI_API_KEY` (free key: https://aistudio.google.com/apikey) and optionally `GEMINI_MODEL`.

## 5. Deactivate when done

```
deactivate
```
