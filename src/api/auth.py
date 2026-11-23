"""Passwordless Supabase adapter. Tokens stay in HttpOnly cookies, never in JS."""
import os
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from typing import Literal

router = APIRouter(prefix="/auth", tags=["Rider accounts"])
COOKIE = "transit_session"


def settings():
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url) or not key:
        raise HTTPException(503, "Account verification is not configured yet. You can continue as a guest.")
    return url, key


def same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = os.getenv("APP_ORIGIN", str(request.base_url).rstrip("/"))
    if not origin or origin != expected or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Request must come from this app.")


async def provider(method, path, payload=None, token=None):
    url, key = settings()
    headers = {"apikey": key}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            result = await client.request(method, url + "/auth/v1/" + path, headers=headers, json=payload)
    except httpx.HTTPError:
        raise HTTPException(503, "Verification service is unavailable. Please try again.")
    if result.status_code >= 400:
        if result.status_code == 429:
            raise HTTPException(429, "Too many attempts. Wait before trying again.")
        if path == "user" and method == "GET":
            raise HTTPException(401, "Your session expired. Sign in again.")
        raise HTTPException(400, "Verification could not be completed. Check your details or request a new code.")
    return result.json() if result.content else {}


class Identity(BaseModel):
    method: Literal["email", "phone"]
    identifier: str = Field(min_length=3, max_length=254)

    def address(self):
        value = self.identifier.strip()
        if self.method == "email":
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                raise HTTPException(422, "Enter a valid email address.")
            return {"email": value.lower()}
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
            raise HTTPException(422, "Use an international phone number, for example +919876543210.")
        return {"phone": value}


class Verification(Identity):
    code: str = Field(pattern=r"^[0-9]{6,10}$")


class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def public_user(user):
    return {"id": user["id"], "name": user.get("user_metadata", {}).get("display_name", ""),
            "email": user.get("email"), "phone": user.get("phone")}


@router.get("/status")
def status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        settings()
        return {"configured": True}
    except HTTPException:
        return {"configured": False}


@router.post("/code", dependencies=[Depends(same_origin)])
async def send_code(body: Identity):
    await provider("POST", "otp", {**body.address(), "create_user": True})
    return {"message": "Check your inbox or messages for a verification code."}


@router.post("/verify", dependencies=[Depends(same_origin)])
async def verify(body: Verification, request: Request, response: Response):
    data = await provider("POST", "verify", {**body.address(), "token": body.code,
                          "type": "email" if body.method == "email" else "sms"})
    response.set_cookie(COOKIE, data["access_token"], httponly=True, samesite="strict",
                        secure=urlsplit(os.getenv("APP_ORIGIN", str(request.base_url))).scheme == "https",
                        max_age=min(int(data.get("expires_in", 3600)), 86400), path="/auth")
    response.headers["Cache-Control"] = "no-store"
    return {"user": public_user(data["user"])}


async def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sign in to manage your profile.")
    return await provider("GET", "user", token=token)


@router.get("/me")
async def me(response: Response, user=Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"user": public_user(user)}


@router.put("/profile", dependencies=[Depends(same_origin)])
async def update_profile(body: Profile, request: Request, response: Response, user=Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Enter your name.")
    result = await provider("PUT", "user", {"data": {"display_name": name}}, request.cookies[COOKIE])
    response.headers["Cache-Control"] = "no-store"
    return {"user": public_user(result)}


@router.post("/logout", dependencies=[Depends(same_origin)])
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/auth", httponly=True, samesite="strict")
    return {"ok": True}
