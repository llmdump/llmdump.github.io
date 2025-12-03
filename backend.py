import os
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
import json

import ddgs

sr = ddgs.DDGS()

def search(query, page=1):
    results = sr.text(query, page=page)
    return results
rootmsg="""
LLM dump backend OK
This is the LLM Dump backend server
Why do we use a backend? Well, we don't use this server for anything other than one auth function,
which is required, otherwise, we would have been instantly hacked.
The server mainly exists because the client secret can't be sent to the client, it has to be secret.
However, everything else is completely frontend.

We also use this server for the web search tool. This is temporary, we will switch web search to the
frontend as well later on.

LLM Dump: llmdump.github.io

How to use API:
/health - returns {"status": "ok"}
/api/exchange?code=... - exchanges the code for an access token,
                         returns {"access_token": "..."} with the access token.
/api/models - proxies the request to https://models.github.ai/catalog/models
              returns the list of models from GitHub AI Models API,
              requires Authorization header with access token
/api/device/start - starts the device flow,
                    returns device code and user code
/api/device/poll?device_code=... - polls the device code for access token,
                                   requires CLIENT_ID and CLIENT_SECRET env variables
                                   returns the access token if successful, otherwise returns an error
/search?query=...&page=1 - performs a web search,
                           requires query, optional page (default 1),
                           returns JSON with search results.
example:
/search?query=coffee&page=1
{
    "results":[
    {
        'title': 'Starbucks Starbucks Coffee Company',
        'href': 'https://www.starbucks.com',
        'body': 'More than just great coffee . Explore the menu, sign up for Starbucks® Rewards, manage your gift card and more.'
    },
    {
        'title': 'The Coffee Bean & Tea Leaf Buy Coffee, Tea, Powders Online | The Coffee Bean & Tea Leaf | The Coffee Bean & Tea Leaf® Online Store',
        'href': 'https://www.coffeebean.com/',
        'body': 'Buy exceptional coffee , tea, powders, equipment and drinkware at The Coffee Bean & Tea Leaf® online store to enjoy our globally sourced products at home.'
    },
    ...]
}
"""
# Environment variables (must be set before starting the server)
CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI")  # e.g. https://<your-gh-pages-domain>/
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")  # comma separated or *

if not CLIENT_ID:
    logging.warning("GITHUB_CLIENT_ID not set")
if not CLIENT_SECRET:
    logging.warning("GITHUB_CLIENT_SECRET not set")
if not OAUTH_REDIRECT_URI:
    logging.warning("OAUTH_REDIRECT_URI not set")

app = FastAPI()
# CORS
if ALLOWED_ORIGINS == "*":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

SESSION_HTTP_TIMEOUT = 15.0
USER_AGENT = "llm-dump-backend/1.0"

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/exchange")
async def exchange(code: Optional[str] = None):
    if not (CLIENT_ID and CLIENT_SECRET and OAUTH_REDIRECT_URI):
        raise HTTPException(500, detail="Server OAuth env not configured")
    if not code:
        raise HTTPException(400, detail="missing code")
    token_url = "https://github.com/login/oauth/access_token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
    }
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=SESSION_HTTP_TIMEOUT) as client:
        r = await client.post(token_url, json=payload, headers=headers)
    data = r.json()
    if r.status_code != 200 or "access_token" not in data:
        logging.warning("OAuth exchange failed: %s", data)
        raise HTTPException(400, detail=data)
    # Only return the token minimal
    return {"access_token": data["access_token"]}

@app.get("/api/models")
async def models(Authorization: Optional[str] = Header(None)):
    if not Authorization:
        raise HTTPException(401, detail="missing Authorization header")
    headers = {"Authorization": Authorization, "Accept": "application/json", "User-Agent": USER_AGENT}
    url = "https://models.github.ai/catalog/models"
    try:
        async with httpx.AsyncClient(timeout=SESSION_HTTP_TIMEOUT) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 401:
            raise HTTPException(401, detail="unauthorized upstream")
        if r.status_code >= 400:
            raise HTTPException(r.status_code, detail=f"upstream error {r.status_code}")
        return r.json()
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("Model proxy error")
        raise HTTPException(500, detail=str(e))

# Optional device flow endpoints (disabled by default). Enable by setting ENABLE_DEVICE_FLOW=1
ENABLE_DEVICE_FLOW = os.environ.get("ENABLE_DEVICE_FLOW") == "1"
if ENABLE_DEVICE_FLOW:
    @app.post("/api/device/start")
    async def device_start():
        if not CLIENT_ID:
            raise HTTPException(500, detail="CLIENT_ID missing")
        url = "https://github.com/login/device/code"
        async with httpx.AsyncClient(timeout=SESSION_HTTP_TIMEOUT) as client:
            r = await client.post(url, data={"client_id": CLIENT_ID, "scope": "read:user read:org"}, headers={"Accept": "application/json"})
        return r.json()

    @app.post("/api/device/poll")
    async def device_poll(device_code: str):
        if not (CLIENT_ID and CLIENT_SECRET):
            raise HTTPException(500, detail="CLIENT env missing")
        url = "https://github.com/login/oauth/access_token"
        async with httpx.AsyncClient(timeout=SESSION_HTTP_TIMEOUT) as client:
            r = await client.post(url, data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            }, headers={"Accept": "application/json"})
        data = r.json()
        return data

@app.get("/search")
async def search_endpoint(query: str, page: Optional[int] = 1):
    if not query:
        raise HTTPException(400, detail="Missing 'query' parameter")
    try:
        page = int(page)
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    try:
        results = search(query, page=page)
    except:
        raise HTTPException(500, detail="Internal server error while searching")
    return JSONResponse(content={"results": results}, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    })
# Simple root
@app.get("/")
async def root():
    return PlainTextResponse(rootmsg)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
