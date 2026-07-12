"""LinePassport browser UI powered by the OkLine client.

This module intentionally uses only the Python standard library for the HTTP
server. The page is a browser wrapper around the existing OkLine client,
so it can run from a source checkout without adding a web framework dependency.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from . import __version__
from ._util import is_mid
from .client import OkLine
from .entities import Group
from .exceptions import LineLoginRequired
from .hmac_signer import LtsmBridge

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LinePassport</title>
  <style>
    :root {
      --bg: #f5f7f8;
      --surface: #ffffff;
      --surface-2: #eef3f1;
      --ink: #18211f;
      --muted: #66736f;
      --line: #d8e1de;
      --accent: #06c755;
      --accent-2: #0a7f7f;
      --danger: #b42318;
      --warn: #9a6700;
      --shadow: 0 16px 40px rgba(18, 29, 26, .08);
    }

    * { box-sizing: border-box; }

    html, body { height: 100%; }

    body {
      margin: 0;
      /* Desktop: the app is exactly viewport height; only inner panes scroll.
         The mobile breakpoint re-enables normal page scrolling. */
      overflow: hidden;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button, input, textarea, select {
      font: inherit;
    }

    button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--surface);
      color: var(--ink);
      padding: 0 12px;
      cursor: pointer;
    }

    button:hover { border-color: #9fb2ad; }
    button:disabled { cursor: not-allowed; opacity: .55; }

    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
      font-weight: 700;
    }

    button.danger {
      border-color: #f1b9b5;
      color: var(--danger);
    }

    button.link-button {
      min-height: 0;
      border: 0;
      background: transparent;
      color: var(--accent-2);
      padding: 0;
      font-weight: 700;
    }

    button.link-button:hover {
      border-color: transparent;
      text-decoration: underline;
    }

    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }

    aside label { color: #aac0ba; }

    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      padding: 10px 11px;
      outline: none;
    }

    textarea { min-height: 92px; resize: vertical; }

    input:focus, textarea:focus, select:focus {
      border-color: var(--accent-2);
      box-shadow: 0 0 0 3px rgba(10, 127, 127, .13);
    }

    .app-shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(260px, 330px) 1fr;
    }

    aside {
      background: #17211e;
      color: #edf8f2;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      min-width: 0;
    }

    main {
      min-width: 0;
      display: flex;
      flex-direction: column;
    }

    .topbar {
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 22px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .78);
      backdrop-filter: blur(8px);
      position: sticky;
      top: 0;
      z-index: 5;
    }

    .topbar > .row {
      flex: 1 1 520px;
      max-width: 620px;
    }

    .brand {
      display: grid;
      gap: 2px;
      min-width: 0;
    }

    .brand-home {
      min-height: 0;
      border: 0;
      padding: 0;
      background: transparent;
      color: inherit;
      text-align: left;
      cursor: pointer;
    }

    .brand-home:focus-visible {
      outline: 3px solid rgba(6, 199, 85, .32);
      outline-offset: 4px;
      border-radius: 8px;
    }

    .brand h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.1;
      letter-spacing: 0;
    }

    .brand span, .muted {
      color: var(--muted);
    }

    aside .muted { color: #aac0ba; }

    .content {
      width: min(1280px, 100%);
      padding: 20px 22px 28px;
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }

    .section {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .section-head {
      min-height: 52px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-2);
    }

    .section-head h2 {
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }

    .section-body {
      padding: 14px;
      display: grid;
      gap: 12px;
    }

    .profile-box {
      display: grid;
      gap: 10px;
      border: 1px solid rgba(255, 255, 255, .13);
      border-radius: 8px;
      padding: 14px;
      background: rgba(255, 255, 255, .06);
    }

    .profile-name {
      font-size: 20px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }

    .kv {
      display: grid;
      grid-template-columns: 82px minmax(0, 1fr);
      gap: 7px 10px;
      align-items: start;
    }

    .kv div:nth-child(odd) {
      color: #aac0ba;
    }

    .kv div:nth-child(even) {
      overflow-wrap: anywhere;
    }

    .status-row {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 25px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0 9px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    .pill.ok {
      border-color: rgba(6, 199, 85, .35);
      background: rgba(6, 199, 85, .12);
      color: #075f2c;
    }

    .pill.warn {
      border-color: rgba(154, 103, 0, .35);
      background: rgba(154, 103, 0, .12);
      color: var(--warn);
    }

    .grid {
      display: grid;
      gap: 18px;
    }

    .form-grid {
      display: grid;
      gap: 10px;
    }

    .two {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }

    .row {
      display: flex;
      gap: 9px;
      align-items: center;
    }

    .row > * { min-width: 0; }
    .row input { flex: 1; }

    .list {
      display: grid;
      gap: 8px;
      max-height: 420px;
      overflow: auto;
      padding-right: 2px;
    }

    .item {
      display: grid;
      gap: 3px;
      width: 100%;
      min-height: 62px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
      text-align: left;
      align-content: start;
      line-height: 1.35;
    }

    .item:hover {
      border-color: #9fb2ad;
      background: #fbfdfc;
    }

    .item strong {
      overflow-wrap: anywhere;
    }

    .item .mono {
      display: block;
      margin-top: 2px;
    }

    .item-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 7px;
    }

    .item-actions button {
      min-height: 31px;
      padding: 0 9px;
      font-size: 12px;
    }

    .bot-terminal {
      overflow: hidden;
      border: 1px solid #263244;
      border-radius: 8px;
      background: #0b111b;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }

    .bot-terminal-bar {
      display: flex;
      min-height: 42px;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid #263244;
      background: #121a27;
      padding: 0 14px;
      color: #d7e0ec;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }

    .bot-terminal-title {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      font-weight: 700;
    }

    .bot-terminal-prompt { color: #65d987; }

    .bot-terminal-live {
      flex: 0 0 auto;
      color: #76e398;
      font-size: 11px;
      font-weight: 700;
    }

    .bot-log-list {
      display: block;
      min-height: 360px;
      max-height: min(620px, calc(100dvh - 330px));
      overflow: auto;
      padding: 6px 0;
      background: #0b111b;
      color: #cbd5e1;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      scrollbar-color: #475569 #0b111b;
    }

    .bot-log-list:focus-visible {
      outline: 2px solid #65d987;
      outline-offset: -2px;
    }

    .bot-log-item {
      min-width: 0;
      border-bottom: 1px solid rgba(148, 163, 184, 0.13);
      padding: 9px 14px 10px;
      line-height: 1.45;
    }

    .bot-log-item:last-child { border-bottom: 0; }

    .bot-log-item:hover { background: rgba(148, 163, 184, 0.06); }

    .bot-log-item .log-main {
      display: grid;
      gap: 5px;
      min-width: 0;
    }

    .bot-log-item .log-line {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 6px 9px;
      min-width: 0;
    }

    .bot-log-item .log-time {
      flex: 0 0 auto;
      color: #7f8da3;
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }

    .bot-log-item .log-status {
      flex: 0 0 auto;
      font-size: 12px;
      font-weight: 700;
    }

    .bot-log-item .log-status.ok { color: #65d987; }
    .bot-log-item .log-status.warn { color: #ff8181; }

    .bot-log-item .log-action {
      color: #f3f7fb;
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .bot-log-item .log-meta {
      min-width: 0;
      color: #9ba8b9;
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .bot-log-item .log-detail {
      margin: 0;
      color: #b7c3d4;
      font: inherit;
      font-size: 12px;
      line-height: 1.55;
      max-height: 3.1em;
      overflow: hidden;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }

    .bot-log-item .log-detail::before {
      content: "> ";
      color: #5fce80;
    }

    .bot-log-item .log-detail.log-full-detail {
      max-height: none;
      overflow: visible;
    }

    .bot-log-list .terminal-empty {
      display: block;
      padding: 18px 14px;
      color: #7f8da3;
      font-size: 12px;
    }

    .bot-log-list .terminal-empty::before {
      content: "$ ";
      color: #65d987;
    }

    .mono {
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .message-list {
      display: grid;
      gap: 10px;
      min-height: 260px;
      max-height: 540px;
      overflow: auto;
      background: #f0f4f3;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }

    .bubble {
      max-width: min(620px, 92%);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px 10px;
      justify-self: start;
      overflow-wrap: anywhere;
    }

    .bubble.me {
      justify-self: end;
      border-color: rgba(6, 199, 85, .28);
      background: #e8f8ef;
    }

    .bubble .by {
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 12px;
    }

    .login-panel {
      display: none;
      width: min(760px, calc(100% - 32px));
      margin: 28px auto;
    }

    .login-panel.active { display: block; }
    .workspace.hidden { display: none; }

    .qr {
      display: grid;
      place-items: center;
      min-height: 240px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: auto;
      padding: 14px;
    }

    .qr svg {
      width: min(320px, 100%);
      height: auto;
    }

    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: min(420px, calc(100vw - 36px));
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      box-shadow: var(--shadow);
      opacity: 0;
      visibility: hidden;
      transform: translateY(10px);
      transition: .18s ease;
      pointer-events: none;
      z-index: 20;
    }

    .toast.show {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
      pointer-events: auto;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 280px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0f1715;
      color: #eef8f3;
      padding: 12px;
    }

    .app-hidden, .hidden { display: none !important; }

    .auth-screen {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background: #eef4f8;
    }

    .auth-card {
      width: min(420px, 100%);
      display: grid;
      gap: 16px;
      border: 1px solid #d8e3ed;
      border-radius: 8px;
      background: #fff;
      padding: 24px;
      box-shadow: 0 18px 50px rgba(15, 23, 42, .12);
    }

    .auth-switch {
      justify-content: center;
      gap: 6px;
      font-size: 13px;
    }

    .app-shell {
      height: 100vh;
      min-height: 0;
      overflow: hidden;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      grid-template-columns: 1fr;
      background: #f3f7fb;
    }

    .topbar {
      min-height: 66px;
      display: grid;
      grid-template-columns: minmax(180px, 260px) minmax(220px, 360px) minmax(0, 1fr);
      align-items: center;
      gap: 14px;
      padding: 10px 16px;
      border-bottom: 1px solid #dbe5ee;
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 10;
    }

    .topbar .brand h1 { font-size: 18px; }

    .account-picker {
      color: #475569;
      font-size: 12px;
      font-weight: 700;
    }

    .top-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      min-width: 0;
    }

    .top-actions button { min-width: 72px; }

    .settings-wrap {
      position: relative;
    }

    .settings-menu {
      position: absolute;
      top: calc(100% + 8px);
      right: 0;
      z-index: 30;
      width: 230px;
      display: grid;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px;
      box-shadow: var(--shadow);
    }

    .settings-menu button {
      width: 100%;
      justify-content: flex-start;
      text-align: left;
    }

    .settings-panel {
      width: min(1040px, calc(100% - 32px));
      margin: 28px auto;
      display: none;
    }

    .settings-panel.active { display: block; }

    .settings-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .settings-tabs button.active {
      border-color: rgba(6, 199, 85, .45);
      background: rgba(6, 199, 85, .1);
      color: #075f2c;
      font-weight: 700;
    }

    .settings-pane {
      display: none;
      gap: 14px;
    }

    .settings-pane.active { display: grid; }

    .management-list {
      display: grid;
      gap: 8px;
    }

    .management-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
    }

    .management-row-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 7px;
    }

    .checkbox-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
    }

    .checkbox-row {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px 10px;
      color: var(--ink);
      font-size: 13px;
      font-weight: 600;
    }

    .checkbox-row input {
      width: auto;
    }

    .main-stage {
      min-height: 0;
      display: grid;
      /* Single row that fills the space left under the topbar; the visible
         child (workspace / account gate / login) stretches to fill it. */
      grid-template-rows: minmax(0, 1fr);
      overflow: auto;
      background: #edf3f8;
    }

    .account-gate {
      display: grid;
      place-items: center;
      padding: 32px;
      text-align: center;
    }

    .account-gate .brand {
      width: min(560px, 100%);
      border: 1px dashed #b9c8d7;
      border-radius: 8px;
      background: #fff;
      padding: 28px;
    }

    .login-panel {
      width: min(840px, calc(100% - 32px));
      min-height: 0;
      margin: 28px auto;
    }

    .login-panel.active { display: block; }

    body.login-page-scroll {
      height: auto;
      min-height: 100%;
      overflow-y: auto;
    }

    body.login-page-scroll .app-shell {
      height: auto;
      min-height: 100vh;
      overflow: visible;
    }

    body.login-page-scroll .main-stage {
      display: block;
      overflow: visible;
    }

    .login-body {
      display: grid;
      gap: 18px;
    }

    .login-steps {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .login-steps li {
      min-height: 50px;
      display: grid;
      align-content: center;
      gap: 2px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }

    .login-steps li.active {
      border-color: rgba(6, 199, 85, .45);
      background: rgba(6, 199, 85, .1);
      color: #075f2c;
    }

    .login-steps li.done {
      border-color: #b7d8c4;
      background: #f2fbf5;
      color: #0f5132;
    }

    .login-step {
      display: none;
    }

    .login-step.active {
      display: grid;
      gap: 14px;
    }

    .wizard-center {
      min-height: 360px;
      display: grid;
      place-items: center;
      text-align: center;
      border: 1px dashed #b9c8d7;
      border-radius: 8px;
      background: #fff;
      padding: 28px;
    }

    .wizard-center-inner {
      display: grid;
      gap: 12px;
      justify-items: center;
      width: min(460px, 100%);
    }

    .wizard-center h3,
    .login-step h3 {
      margin: 0;
      font-size: 24px;
      letter-spacing: 0;
    }

    .wizard-start {
      min-width: 180px;
      min-height: 48px;
      font-size: 16px;
    }

    .wizard-card {
      display: grid;
      gap: 14px;
      width: min(620px, 100%);
      margin: 0 auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 18px;
    }

    .pin-display {
      min-width: min(420px, 100%);
      border: 1px solid rgba(6, 199, 85, .35);
      border-radius: 8px;
      background: #f2fbf5;
      color: #075f2c;
      padding: 24px 28px;
      font-size: clamp(56px, 12vw, 116px);
      font-weight: 800;
      line-height: 1;
      letter-spacing: 0;
      font-variant-numeric: tabular-nums;
      text-align: center;
    }

    .workspace {
      height: 100%;
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 0;
      padding: 0;
      overflow: hidden;
    }

    /* Top-level tab bar (LINE / Tools / Bot). */
    .tabbar {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding: 8px 12px 0;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }

    .tabbar .tab {
      min-height: 40px;
      border: 0;
      border-bottom: 3px solid transparent;
      border-radius: 8px 8px 0 0;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
      padding: 0 18px;
    }

    .tabbar .tab:hover { color: var(--ink); background: #f2f6f5; }

    .tabbar .tab[aria-selected="true"] {
      color: #075f2c;
      border-bottom-color: var(--accent);
      background: rgba(6, 199, 85, .08);
    }

    .tabbar .tab:focus-visible {
      outline: 2px solid var(--accent-2);
      outline-offset: 2px;
    }

    .tab-panels {
      min-height: 0;
      position: relative;
      overflow: hidden;
    }

    .tab-panel { display: none; height: 100%; min-height: 0; }
    .tab-panel.active { display: block; }

    .tab-panel[data-tab-panel="line"].active {
      display: grid;
      grid-template-columns: minmax(280px, 330px) minmax(360px, 1fr);
      gap: 0;
      padding: 0;
      overflow: hidden;
    }

    .tab-panel[data-tab-panel="tools"].active,
    .tab-panel[data-tab-panel="bot"].active,
    .tab-panel[data-tab-panel="ai"].active {
      overflow: auto;
      padding: 16px;
    }

    .ai-preview { margin-top: 4px; }
    .ai-preview img {
      max-width: 100%;
      display: block;
      border-radius: 12px;
      border: 1px solid var(--line);
    }

    /* Date fields: a dd/mm/yyyy text display sits over the native date input so
       the calendar picker stays, but the shown format is not the browser locale's. */
    .date-field { position: relative; display: block; min-width: 0; }
    .date-field > .date-text { width: 100%; cursor: pointer; }
    .date-field > .date-native {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      margin: 0;
      opacity: 0;
      cursor: pointer;
    }
    /* keep the overlay fully transparent even when account-gating disables it
       (otherwise the native mm/dd/yyyy text bleeds through the dd/mm/yyyy mirror) */
    .date-field > .date-native:disabled { cursor: default; opacity: 0; }

    .tab-panel-inner {
      width: min(760px, 100%);
      margin: 0 auto;
      display: grid;
      gap: 12px;
      align-content: start;
    }

    /* Contacts sub-tabs (people / groups): LINE-style underline text tabs. */
    .subtabs {
      display: flex;
      gap: 6px;
    }

    .subtabs .subtab {
      min-height: 40px;
      padding: 0 10px;
      border: 0;
      border-bottom: 2px solid transparent;
      border-radius: 0;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
      font-size: 14px;
    }

    .subtabs .subtab:hover { color: var(--ink); }

    .subtabs .subtab[aria-selected="true"] {
      color: #06251a;
      border-bottom-color: var(--accent);
    }

    .subtabs .subtab:focus-visible {
      outline: 2px solid var(--accent-2);
      outline-offset: 2px;
    }

    .icon-btn {
      min-height: 32px;
      min-width: 36px;
      padding: 0 8px;
      font-size: 16px;
      line-height: 1;
    }

    .settings-menu .menu-sep {
      height: 1px;
      margin: 4px 2px;
      background: var(--line);
    }

    aside.line-sidebar,
    aside.tools-column {
      min-width: 0;
      display: grid;
      align-content: start;
      gap: 12px;
      padding: 0;
      background: transparent;
      color: var(--ink);
    }

    aside.line-sidebar label,
    aside.tools-column label {
      color: var(--muted);
    }

    .profile-box {
      background: #fff;
      border-color: #dbe5ee;
      color: var(--ink);
    }

    .profile-box .kv div:nth-child(odd) {
      color: var(--muted);
    }

    .section {
      box-shadow: 0 8px 24px rgba(15, 23, 42, .07);
    }

    .section-head {
      background: #f8fafc;
    }

    .contact-pane {
      min-height: 0;
    }

    .contact-list,
    .group-list {
      max-height: calc(100vh - 320px);
    }

    .chat-shell {
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      overflow: hidden;
    }

    .chat-header {
      min-height: 62px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 520px);
      align-items: center;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }

    .chat-header h2 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }

    .message-list {
      min-height: 0;
      max-height: none;
      border: 0;
      border-radius: 0;
      background: #dfeaf3;
      padding: 18px;
    }

    .bubble {
      border-radius: 18px 18px 18px 6px;
      border: 0;
      box-shadow: 0 1px 2px rgba(15, 23, 42, .08);
    }

    .bubble.me {
      border: 0;
      border-radius: 18px 18px 6px 18px;
      background: #d6f5c9;
    }

    .composer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
      padding: 12px;
      border-top: 1px solid var(--line);
      background: #fff;
    }

    .composer textarea {
      min-height: 48px;
      max-height: 140px;
      resize: vertical;
    }

    .tools-column {
      height: 100%;
      overflow: auto;
      padding-right: 2px !important;
    }

    .tools-column .section {
      overflow: visible;
    }

    .schedule-list {
      max-height: 260px;
    }

    .compact {
      gap: 7px;
    }

    .compact button,
    .compact select {
      min-height: 36px;
    }

    [data-requires-account]:disabled {
      opacity: .45;
      cursor: not-allowed;
    }

    @media (prefers-reduced-motion: reduce) {
      .toast { transition: none; }
    }

    @media (max-width: 980px) {
      /* Narrow / mobile: fall back to normal page scrolling. */
      html, body { height: auto; }
      body { overflow: auto; }
      .app-shell {
        height: auto;
        min-height: 100vh;
        overflow: visible;
      }
      .main-stage {
        grid-template-rows: auto;
        overflow: visible;
      }
      .topbar {
        grid-template-columns: 1fr;
        align-items: stretch;
      }
      .top-actions { justify-content: flex-start; flex-wrap: wrap; }
      .workspace {
        height: auto;
        min-height: 0;
        overflow: visible;
        grid-template-columns: 1fr;
      }
      .tab-panels { overflow: visible; }
      .tab-panel[data-tab-panel="line"].active {
        grid-template-columns: 1fr;
        overflow: visible;
      }
      .line-list {
        border-right: 0;
        border-bottom: 1px solid var(--line);
        max-height: 46vh;
      }
      .line-convo { min-height: 620px; }
      .line-messages { min-height: 320px; }
      .chat-shell { min-height: 640px; }
      .chat-header { grid-template-columns: 1fr; }
      .login-steps { grid-template-columns: 1fr 1fr; }
      .wizard-center { min-height: 300px; }
      .content { grid-template-columns: 1fr; }
      .two { grid-template-columns: 1fr; }
    }

    @media (max-width: 620px) {
      .topbar {
        align-items: stretch;
        flex-direction: column;
      }

      .content { padding: 14px; }
      .row { align-items: stretch; flex-direction: column; }
      button { width: 100%; }
    }

    /* Missing rule: Create User used .two-col but only .two existed. */
    .two-col {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 10px;
    }

    @media (max-width: 980px) {
      .two-col { grid-template-columns: 1fr; }
    }

    /* Advanced-only surfaces are enabled by default; keep the class hook for
       legacy markup while removing the manual Advanced menu toggle. */
    body:not(.advanced) .advanced-only { display: none !important; }

    #langToggle.active {
      border-color: rgba(6, 199, 85, .45);
      background: rgba(6, 199, 85, .1);
      color: #075f2c;
      font-weight: 700;
    }

    .user-name-pill {
      max-width: 160px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* Inline loading / empty / retry states for lists. */
    .state-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px;
      color: var(--muted);
      font-size: 13px;
    }

    .state-row.error { color: var(--danger); }
    .state-row button { min-height: 30px; padding: 0 10px; }

    .spinner {
      width: 15px;
      height: 15px;
      border: 2px solid var(--line);
      border-top-color: var(--accent-2);
      border-radius: 50%;
      display: inline-block;
      animation: okspin .7s linear infinite;
      flex: 0 0 auto;
    }

    @keyframes okspin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }

    .bubble .at {
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      text-align: right;
    }

    /* Error toasts can be dismissed and also expire automatically. */
    .toast { display: flex; align-items: flex-start; gap: 10px; }
    .toast .toast-text { flex: 1; overflow-wrap: anywhere; }
    .toast .toast-close {
      min-height: 0;
      border: 0;
      background: transparent;
      color: inherit;
      font-size: 18px;
      line-height: 1;
      padding: 0 2px;
      width: auto;
      cursor: pointer;
      opacity: .7;
    }
    .toast .toast-close:hover { opacity: 1; }

    .account-switch-overlay {
      position: fixed;
      inset: 0;
      z-index: 60;
      display: grid;
      place-items: center;
      padding: 24px;
      background: rgba(248, 250, 252, .76);
      backdrop-filter: blur(2px);
    }

    .account-switch-card {
      width: min(320px, calc(100vw - 40px));
      display: grid;
      justify-items: center;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      padding: 24px;
      text-align: center;
      box-shadow: 0 24px 60px rgba(15, 23, 42, .22);
    }

    .account-switch-card .spinner {
      width: 30px;
      height: 30px;
      border-width: 3px;
    }

    .account-switch-card strong {
      font-size: 18px;
      line-height: 1.25;
    }

    .account-switch-card .muted {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .qr-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
      justify-content: center;
    }

    .schedule-summary {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .inline-edit {
      display: grid;
      gap: 10px;
      margin-top: 10px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }

    /* Lightweight modal for account/user edit + delete dialogs. */
    .modal-overlay {
      position: fixed;
      inset: 0;
      z-index: 40;
      display: grid;
      place-items: center;
      padding: 20px;
      background: rgba(15, 23, 42, .42);
    }

    .modal {
      width: min(460px, 100%);
      display: grid;
      gap: 14px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      padding: 20px;
      box-shadow: 0 24px 60px rgba(15, 23, 42, .3);
    }

    .modal h3 { margin: 0; font-size: 18px; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
    .modal-actions button { min-width: 96px; }

    /* ============================================================
       LINE Desktop-style layout for the LINE tab
       (list column + conversation pane)
       ============================================================ */
    :root {
      --line-msg-bg: #e9eef2;
      --line-out: #06c755;
      --line-in: #ffffff;
    }

    /* (2) list column — a LIGHT surface, so force dark ink text instead of
       inheriting the dark-sidebar near-white colour from the base `aside`. */
    .line-list {
      min-width: 0;
      min-height: 0;
      display: flex;
      flex-direction: column;
      background: #fff;
      color: var(--ink);
      border-right: 1px solid var(--line);
      overflow: hidden;
    }

    .line-list label { color: var(--muted); }

    .line-me {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }

    .line-me-info { min-width: 0; }
    .line-me .profile-name {
      font-size: 15px;
      font-weight: 800;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .line-me-sub {
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .line-me-pills {
      display: flex;
      flex-direction: column;
      gap: 4px;
      align-items: flex-end;
    }

    .line-me-pills .pill { font-size: 10px; min-height: 20px; padding: 0 7px; }

    .line-me-detail {
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 6px 10px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
      font-size: 12px;
    }

    .line-me-detail div:nth-child(odd) { color: var(--muted); }

    .line-list-tabbar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      border-bottom: 1px solid var(--line);
    }

    .line-list-tabs { flex: 1; min-width: 0; }

    .line-list-tabbar .icon-ghost {
      width: 40px;
      height: 40px;
      min-height: 40px;
      flex: 0 0 auto;
    }

    .line-list-tabbar .icon-ghost[aria-busy="true"] svg {
      animation: okspin .7s linear infinite;
    }

    @media (prefers-reduced-motion: reduce) {
      .line-list-tabbar .icon-ghost[aria-busy="true"] svg { animation: none; }
    }

    /* LINE-style search field with a magnifier icon inside */
    .line-search {
      display: flex;
      align-items: center;
      gap: 6px;
      margin: 10px 12px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #f2f5f7;
    }

    .line-search:focus-within {
      border-color: var(--accent-2);
      box-shadow: 0 0 0 3px rgba(10, 127, 127, .12);
    }

    .search-icon {
      border: 0;
      background: transparent;
      color: var(--muted);
      min-height: 34px;
      width: 26px;
      padding: 0;
      display: grid;
      place-items: center;
    }

    .search-icon svg { width: 16px; height: 16px; }
    .search-icon:hover { border-color: transparent; color: var(--ink); }

    .line-search input {
      border: 0;
      background: transparent;
      padding: 9px 0;
      box-shadow: none !important;
    }

    .line-search input:focus { box-shadow: none; }

    .line-rows {
      flex: 1;
      min-height: 0;
      max-height: none;
      overflow: auto;
      display: block;
      padding: 4px 6px 10px;
    }

    /* rich contact / group row */
    .line-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      width: 100%;
      min-height: 60px;
      border: 0;
      border-radius: 10px;
      background: transparent;
      padding: 8px 10px;
      text-align: left;
    }

    .line-row:hover { background: #f2f6f5; border-color: transparent; }

    .line-row.selected {
      background: rgba(6, 199, 85, .12);
    }

    .line-row-main { min-width: 0; }

    .line-row-name {
      font-weight: 700;
      font-size: 14px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .line-row-sub {
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .line-row-time {
      color: var(--muted);
      font-size: 11px;
      font-variant-numeric: tabular-nums;
      align-self: start;
    }

    /* deterministic circular avatar */
    .avatar {
      flex: 0 0 auto;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      color: #fff;
      font-weight: 800;
      font-size: 18px;
      background: #9aa7b4;
      overflow: hidden;
      user-select: none;
    }

    .avatar-sm { width: 30px; height: 30px; font-size: 13px; }
    .avatar-lg { width: 46px; height: 46px; font-size: 19px; }

    /* (3) conversation pane */
    .line-convo {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr) auto;
      background: var(--line-msg-bg);
      overflow: hidden;
    }

    .convo-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 16px;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }

    .convo-title { min-width: 0; }
    .convo-title h2 {
      margin: 0;
      font-size: 16px;
      font-weight: 800;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .convo-sub {
      display: block;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .convo-actions { display: flex; align-items: center; gap: 2px; }

    .icon-ghost {
      width: 34px;
      height: 34px;
      min-height: 34px;
      display: grid;
      place-items: center;
      border: 0;
      border-radius: 8px;
      background: transparent;
      color: var(--muted);
      padding: 0;
    }

    .icon-ghost svg { width: 19px; height: 19px; }
    .icon-ghost:hover { background: #eef3f1; color: var(--ink); border-color: transparent; }
    .icon-ghost:focus-visible { outline: 2px solid var(--accent-2); outline-offset: 2px; }

    .convo-advanced {
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 8px 16px;
      background: #f8fafc;
      border-bottom: 1px solid var(--line);
    }

    .convo-advanced input { flex: 1; }
    .convo-advanced select { width: auto; }

    .line-messages {
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
      overflow: auto;
      padding: 16px 18px;
      background: var(--line-msg-bg);
      border: 0;
      border-radius: 0;
    }

    .date-chip {
      align-self: center;
      margin: 10px 0;
    }

    .date-chip span {
      display: inline-block;
      padding: 3px 12px;
      border-radius: 999px;
      background: rgba(15, 23, 42, .16);
      color: #fff;
      font-size: 12px;
      font-weight: 700;
    }

    .msg {
      display: flex;
      align-items: flex-end;
      gap: 8px;
      max-width: 100%;
      margin: 1px 0;
    }

    .msg.theirs { justify-content: flex-start; }
    .msg.mine { justify-content: flex-end; }

    .msg-col { display: flex; flex-direction: column; min-width: 0; max-width: 68%; }
    .msg.mine .msg-col { align-items: flex-end; }

    .msg-sender {
      margin: 0 0 3px 6px;
      color: #55636e;
      font-size: 12px;
      font-weight: 600;
    }

    .msg-line { display: flex; align-items: flex-end; gap: 6px; }

    /* Chat bubbles hug their content and sit on opposite sides:
       received (left, LINE green) — own (right, light grey). */
    .line-messages .bubble {
      width: fit-content;
      max-width: 95%;
      margin-right: auto;   /* received hugs the left, ~5% gap on the right */
      margin-left: 0;
      border: 0;
      border-radius: 14px 14px 14px 4px;
      background: #ededed;   /* received: light grey */
      color: var(--ink);
      padding: 8px 12px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, .12);
      overflow-wrap: anywhere;
    }

    .line-messages .bubble.me {
      margin-left: auto;   /* own hugs the right, ~5% gap on the left */
      margin-right: 0;
      border-radius: 14px 14px 4px 14px;
      background: var(--line-out);   /* own: LINE green */
      color: #fff;
    }

    /* Sender name + timestamp, legible on each bubble colour. */
    .line-messages .bubble .by,
    .line-messages .bubble .at { color: #6b7680; }
    .line-messages .bubble.me .by,
    .line-messages .bubble.me .at { color: rgba(255, 255, 255, .88); }

    .msg-text { white-space: pre-wrap; }

    /* sticker / image chip — legible on each bubble colour */
    .msg-chip {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 8px;
      background: rgba(15, 23, 42, .08);
      color: var(--ink);
      font-size: 13px;
    }

    .bubble.me .msg-chip { background: rgba(255, 255, 255, .85); color: #08301d; }

    .msg-time {
      flex: 0 0 auto;
      color: #7b8a94;
      font-size: 10px;
      font-variant-numeric: tabular-nums;
      padding-bottom: 2px;
      white-space: nowrap;
    }

    /* LINE-style bottom composer */
    .line-composer {
      display: flex;
      align-items: flex-end;
      gap: 6px;
      padding: 10px 14px;
      background: #fff;
      border-top: 1px solid var(--line);
    }

    .line-composer textarea {
      flex: 1;
      min-height: 40px;
      max-height: 132px;
      border-radius: 12px;
      background: #f2f5f7;
      border-color: transparent;
      padding: 9px 12px;
      resize: none;
    }

    .line-composer textarea:focus {
      background: #fff;
      border-color: var(--accent-2);
    }

    .icon-toggle {
      width: 38px;
      height: 38px;
      min-height: 38px;
      display: grid;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 50%;
      background: #fff;
      color: var(--muted);
      padding: 0;
    }

    .icon-toggle svg { width: 18px; height: 18px; }
    .icon-toggle:hover { border-color: #9fb2ad; color: var(--ink); }
    .icon-toggle[aria-pressed="true"] {
      border-color: rgba(6, 199, 85, .5);
      background: rgba(6, 199, 85, .12);
      color: #075f2c;
    }

    .send-btn {
      width: 40px;
      height: 40px;
      min-height: 40px;
      display: grid;
      place-items: center;
      border: 0;
      border-radius: 50%;
      background: var(--accent);
      color: #fff;
      padding: 0;
    }

    .send-btn svg { width: 19px; height: 19px; }
    .send-btn:hover { background: #05b34c; border-color: transparent; }
    .send-btn:disabled { background: #b7d8c4; }
    .send-btn.busy, .icon-toggle.busy { opacity: .6; }
    .line-composer .icon-ghost { flex: 0 0 auto; }
    .line-composer { position: relative; }
    .emoji-pop {
      position: absolute;
      left: 8px;
      bottom: calc(100% + 6px);
      width: 268px;
      max-height: 220px;
      overflow-y: auto;
      display: grid;
      grid-template-columns: repeat(8, 1fr);
      gap: 2px;
      padding: 8px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      z-index: 30;
    }
    .emoji-pop .emoji-item {
      border: 0;
      background: transparent;
      cursor: pointer;
      font-size: 20px;
      line-height: 1;
      padding: 4px;
      border-radius: 8px;
    }
    .emoji-pop .emoji-item:hover { background: var(--surface-2); }
    .msg-media {
      display: block;
      max-width: min(240px, 60vw);
      max-height: 280px;
      border-radius: 12px;
      cursor: pointer;
    }
    .msg-sticker { display: block; width: 124px; height: 124px; object-fit: contain; }
    .line-messages .bubble.has-media {
      background: transparent;
      border: 0;
      padding: 0;
    }
    .line-messages .bubble.has-media .by,
    .line-messages .bubble.has-media .at { color: var(--muted); }
    .msg-file {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      color: inherit;
      text-decoration: none;
      font-weight: 600;
    }
    .msg-file:hover { text-decoration: underline; }
    .msg-file svg { width: 20px; height: 20px; flex: 0 0 auto; }
    .ph-help { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
    .ph-help .muted { font-size: 12px; }
    .ph-chip {
      min-height: 0;
      padding: 2px 8px;
      font-size: 12px;
      font-family: ui-monospace, monospace;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--ink);
      cursor: pointer;
    }
    .ph-chip:hover { border-color: var(--line-out); }
    .ph-preview { font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .hm-pick { display: flex; align-items: center; gap: 5px; }
    .hm-pick select { width: auto; flex: 1; }
    .pattern-list {
      display: flex;
      flex-direction: column;
      gap: 4px;
      max-height: 160px;
      overflow-y: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 8px;
    }
    .pattern-item { display: flex; align-items: center; gap: 8px; cursor: pointer; }
    .pattern-item input[type="checkbox"] { width: auto; flex: 0 0 auto; }
    .pattern-item .pattern-name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--ink);
      font-weight: 500;
    }
    .pattern-del { flex: 0 0 auto; min-height: 0; padding: 1px 8px; font-size: 15px; line-height: 1.3; }
    #scheduleTargetTabs { margin-bottom: 5px; }
    .bot-shell {
      display: grid;
      gap: 12px;
    }
    .pattern-category-heading {
      padding: 5px 3px 2px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .pattern-main-column { min-width: 0; display: grid; gap: 12px; }
    #patternFormSection .section-head h3,
    .pattern-list-panel .section-head h3,
    .pattern-category-list-panel .section-head h3 { margin: 0; font-size: 15px; }
    .pattern-filter-row {
      grid-template-columns: minmax(0, 1fr) 44px;
      align-items: end;
      gap: 10px;
    }
    .pattern-settings-button {
      width: 44px;
      min-width: 44px;
      height: 44px;
      min-height: 44px;
      padding: 0;
      font-size: 23px;
      line-height: 1;
    }
    .pattern-form-actions { justify-content: flex-end; padding-top: 4px; }
    #patternManageList,
    #patternCategoryManageList { max-height: none; overflow: visible; }
    #patternManageList .item { padding: 14px; gap: 6px; }
    #patternCategoryManageList .item { padding: 14px; gap: 6px; }
    .pattern-category-badge {
      display: inline-flex;
      width: fit-content;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--surface-2);
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }
    @media (max-width: 620px) {
      .pattern-page-header .section-head { align-items: stretch; flex-direction: column; }
      .pattern-page-header .section-head > .primary { width: 100%; }
    }
    .bot-page-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .bot-page-nav button {
      min-height: 40px;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
      cursor: pointer;
    }
    .bot-page-nav button.active {
      border-color: rgba(6, 199, 85, .35);
      background: rgba(6, 199, 85, .12);
      color: var(--accent-2);
    }
    .bot-page-nav button:not(.active):hover {
      border-color: var(--line);
      background: var(--surface-2);
      color: var(--ink);
    }
    .bot-page {
      display: grid;
      gap: 12px;
    }

    @media (prefers-color-scheme: dark) {
      .line-convo { --line-msg-bg: #202b36; }
      .line-messages { background: #202b36; }
      /* received stays LINE green + white; own becomes dark grey + light text */
      .line-messages .bubble { background: #3a4650; color: #eef2f5; }
      .line-messages .bubble.me { background: var(--line-out); color: #fff; }
      .line-messages .bubble .msg-chip { background: rgba(255, 255, 255, .14); color: #eef2f5; }
      .line-messages .bubble .by,
      .line-messages .bubble .at { color: #b8c2cc; }
      .msg-sender { color: #a7b3bd; }
      .msg-time { color: #8494a0; }
      .date-chip span { background: rgba(255, 255, 255, .22); }
    }
  </style>
</head>
<body class="advanced">
  <section class="auth-screen hidden" id="authPanel">
    <form class="auth-card" id="authForm" novalidate>
      <div class="brand">
        <h1>LinePassport</h1>
        <span id="authModeLabel" data-i18n="auth.secure">Secure access</span>
      </div>
      <label>
        <span id="authIdentityLabel" data-i18n="auth.email">Email</span>
        <input id="webUsernameInput" type="email" inputmode="email" autocomplete="email" placeholder="name@example.com" required>
      </label>
      <label id="registrationNameRow" class="hidden">
        <span data-i18n="auth.displayname">Display name</span>
        <input id="registrationNameInput" type="text" autocomplete="name" maxlength="80" placeholder="Your name">
      </label>
      <label>
        <span data-i18n="auth.password">Password</span>
        <input id="webPasswordInput" type="password" autocomplete="current-password">
      </label>
      <label id="confirmPasswordRow" class="hidden">
        <span data-i18n="auth.confirm">Confirm password</span>
        <input id="confirmPasswordInput" type="password" autocomplete="new-password">
      </label>
      <button id="webAuthButton" class="primary" type="submit" disabled data-i18n="auth.continue">Continue</button>
      <div class="row auth-switch hidden" id="authSwitchRow">
        <span class="muted" id="authSwitchText" data-i18n="auth.need_account">Need an account?</span>
        <button id="authModeToggle" class="link-button" type="button" data-i18n="auth.register">Register</button>
      </div>
      <span class="muted" id="webAuthHint" data-i18n="auth.hint_checking">Checking web auth</span>
    </form>
  </section>

  <div class="app-shell app-hidden" id="appShell">
    <header class="topbar">
      <button class="brand brand-home" id="homeLogoButton" type="button" data-i18n-title="topbar.home" title="Home" aria-label="Home">
        <h1>LinePassport</h1>
      </button>
      <label class="account-picker">
        <span data-i18n="topbar.account">LINE Account</span>
        <select id="accountSelect"></select>
      </label>
      <div class="top-actions">
        <button id="refreshAllButton" data-i18n="topbar.refresh">Refresh</button>
        <div class="settings-wrap">
          <button id="settingsButton" aria-haspopup="menu" aria-expanded="false" data-i18n="topbar.settings">Settings</button>
          <div class="settings-menu hidden" id="settingsMenu" role="menu">
            <button id="langToggle" type="button" role="menuitem" title="ภาษา / Language">EN</button>
            <div class="menu-sep" role="separator"></div>
            <button id="changePasswordMenuButton" role="menuitem" data-i18n="topbar.change_password">Change Password</button>
            <button id="accountManagementMenuButton" role="menuitem" data-i18n="topbar.line_account_management">LINE Account Management</button>
            <button id="userManagementMenuButton" role="menuitem" data-i18n="topbar.user_management">User Management</button>
            <button id="webLogoutButton" role="menuitem" data-i18n="topbar.logout">Log out</button>
          </div>
        </div>
      </div>
    </header>

    <main class="main-stage">
      <section class="account-gate" id="accountGate">
        <div class="brand" id="accountGateDefault">
          <h1 data-i18n="gate.title">Select a LINE account</h1>
          <span data-i18n="gate.body">Choose an account from the dropdown above before using Contacts, Chat, Send, or Scheduler.</span>
        </div>
        <div class="brand hidden" id="accountGateNoAccess">
          <h1 data-i18n="gate.none_shared_title">No LINE account shared with you</h1>
          <span data-i18n="gate.none_shared_body">Ask an administrator to grant you access to a LINE account.</span>
        </div>
      </section>

      <section class="settings-panel section" id="settingsPanel">
        <div class="section-head">
          <div>
            <h2 data-i18n="settings.title">Settings</h2>
            <span class="muted" id="settingsSubtitle" data-i18n="settings.subtitle">Manage access and LINE accounts.</span>
          </div>
          <button id="settingsBackButton" data-i18n="settings.back">Back</button>
        </div>
        <div class="section-body login-body">
          <div class="settings-tabs">
            <button id="passwordTabButton" data-settings-pane="password" data-i18n="topbar.change_password">Change Password</button>
            <button id="accountsTabButton" data-settings-pane="accounts" data-i18n="topbar.line_account_management">LINE Account Management</button>
            <button id="usersTabButton" data-settings-pane="users" data-i18n="topbar.user_management">User Management</button>
          </div>

          <div class="settings-pane" id="passwordPane">
            <div class="wizard-card">
              <h3 data-i18n="password.title">Change Password</h3>
              <label>
                <span data-i18n="password.current">Current password</span>
                <input id="currentPasswordInput" type="password" autocomplete="current-password">
              </label>
              <label>
                <span data-i18n="password.new">New password</span>
                <input id="newPasswordInput" type="password" autocomplete="new-password">
              </label>
              <label>
                <span data-i18n="password.confirm">Confirm new password</span>
                <input id="confirmNewPasswordInput" type="password" autocomplete="new-password">
              </label>
              <button id="changePasswordButton" class="primary" data-i18n="password.update">Update Password</button>
            </div>
          </div>

          <div class="settings-pane" id="accountsPane">
            <div class="section-head">
              <div>
                <h3 data-i18n="accounts.title">LINE Accounts</h3>
                <span class="muted" data-i18n="accounts.subtitle">Add, edit, or delete local LINE sessions.</span>
              </div>
              <button id="addAccountButton" class="primary" data-i18n="accounts.add">Add Account</button>
            </div>
            <div class="management-list" id="lineAccountList"></div>
          </div>

          <div class="settings-pane" id="usersPane">
            <div class="wizard-card hidden" id="secureCard">
              <h3 data-i18n="secure.title">Require a password to open LinePassport</h3>
              <span class="muted" data-i18n="secure.body">LinePassport currently opens without a login. Set a web password (separate from LINE, and not recoverable) to require sign-in.</span>
              <button id="secureButton" class="primary" data-i18n="secure.action">Set a password</button>
            </div>

            <div class="section-head" id="userManagementHead">
              <div>
                <h3 data-i18n="users.title">Users &amp; Access</h3>
                <span class="muted" data-i18n="users.subtitle">Assign roles and choose which LINE accounts each user can access.</span>
              </div>
            </div>
            <div class="wizard-card" id="createUserCard">
              <h3 data-i18n="users.create">Create User</h3>
              <div class="two-col">
                <label>
                  <span data-i18n="users.email">Email</span>
                  <input id="newUserEmail" type="email" inputmode="email" autocomplete="off" placeholder="operator@example.com">
                </label>
                <label>
                  <span data-i18n="users.displayname">Display name</span>
                  <input id="newUserDisplayName" autocomplete="off" placeholder="Shop Operator">
                </label>
              </div>
              <div class="two-col">
                <label>
                  <span data-i18n="users.password">Password</span>
                  <input id="newUserPassword" type="password" autocomplete="new-password">
                </label>
                <label>
                  <span data-i18n="users.role">Role</span>
                  <select id="newUserRole"></select>
                </label>
              </div>
              <button id="createUserButton" class="primary" data-i18n="users.create">Create User</button>
            </div>
            <div class="management-list" id="userList"></div>
          </div>
        </div>
      </section>

      <section class="login-panel section" id="loginPanel">
        <div class="section-head">
          <div>
            <h2 data-i18n="login.add_title">Add LINE Account</h2>
            <span class="muted" data-i18n="login.subtitle">Follow the 4 steps to add another LINE session. LinePassport will use the display name from your LINE account.</span>
          </div>
          <div class="row compact">
            <button id="cancelLoginButton" data-i18n="login.back">Back</button>
          </div>
        </div>
        <div class="section-body login-body">
          <ol class="login-steps" id="loginSteps">
            <li data-step="1"><span data-i18n="login.step_1">Step 1</span><strong data-i18n="login.step1">Start</strong></li>
            <li data-step="2"><span data-i18n="login.step_2">Step 2</span><strong data-i18n="login.step2">Scan QR</strong></li>
            <li data-step="3"><span data-i18n="login.step_3">Step 3</span><strong data-i18n="login.step3">Confirm PIN</strong></li>
            <li data-step="4"><span data-i18n="login.step_4">Step 4</span><strong data-i18n="login.step4">Done</strong></li>
          </ol>

          <div class="login-step" data-login-step="1">
            <div class="wizard-center">
              <div class="wizard-center-inner">
                <span class="pill ok" data-i18n="login.step1of4">Step 1 of 4</span>
                <h3 data-i18n="login.start_title">Start adding a LINE account</h3>
                <span class="muted" data-i18n="login.start_hint">This opens a clean login flow. LinePassport will use the display name from your LINE account.</span>
                <button id="beginAddAccountButton" class="primary wizard-start" data-i18n="login.start">Start</button>
              </div>
            </div>
          </div>

          <div class="login-step" data-login-step="2">
            <div class="wizard-card">
              <span class="pill ok" data-i18n="login.step2of4">Step 2 of 4</span>
              <h3 data-i18n="login.scan_title">Scan the QR code</h3>
              <div class="status-row">
                <span class="pill warn" id="loginState" data-i18n="login.idle">Idle</span>
              </div>
              <div class="qr" id="qrBox" tabindex="-1">
                <span class="muted" data-i18n="login.qr_placeholder">QR will appear here</span>
              </div>
              <div class="qr-actions" id="qrActions" hidden>
                <a id="qrLink" class="mono" href="#" target="_blank" rel="noreferrer"></a>
                <button id="qrCopyButton" type="button" data-i18n="common.copy">Copy link</button>
              </div>
            </div>
          </div>

          <div class="login-step" data-login-step="3">
            <div class="wizard-center">
              <div class="wizard-center-inner">
                <span class="pill ok" data-i18n="login.step3of4">Step 3 of 4</span>
                <h3 data-i18n="login.pin_title">Confirm PIN</h3>
                <div class="pin-display mono" id="loginPinReview" tabindex="-1">----</div>
                <span class="muted" data-i18n="login.pin_hint">If LINE asks for a PIN, confirm this number on your LINE device.</span>
              </div>
            </div>
          </div>

          <div class="login-step" data-login-step="4">
            <div class="wizard-center">
              <div class="wizard-center-inner">
                <span class="pill ok" data-i18n="login.step4of4">Step 4 of 4</span>
                <h3 data-i18n="login.done_title">Opening account</h3>
                <span class="muted" id="loginDoneText" tabindex="-1" data-i18n="login.done_text">After login finishes, LinePassport will open this LINE account automatically.</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div class="workspace hidden" id="workspace">
        <div class="tabbar" role="tablist" aria-label="LinePassport sections">
          <button class="tab" id="tabLine" type="button" role="tab" aria-selected="true" aria-controls="tabPanelLine" data-tab="line" data-i18n="tabs.line">LINE</button>
          <button class="tab" id="tabTools" type="button" role="tab" aria-selected="false" aria-controls="tabPanelTools" data-tab="tools" data-i18n="tabs.tools">Tools</button>
          <button class="tab" id="tabBot" type="button" role="tab" aria-selected="false" aria-controls="tabPanelBot" data-tab="bot" data-i18n="tabs.bot">Bot</button>
          <button class="tab" id="tabAi" type="button" role="tab" aria-selected="false" aria-controls="tabPanelAi" data-tab="ai" data-i18n="tabs.ai">AI Settings</button>
        </div>
        <div class="tab-panels">
          <div class="tab-panel active" data-tab-panel="line" id="tabPanelLine" role="tabpanel" aria-labelledby="tabLine">
        <aside class="line-list">
          <div class="line-me">
            <div class="avatar avatar-lg" id="profileAvatar" aria-hidden="true"></div>
            <div class="line-me-info">
              <div class="profile-name" id="profileName" data-i18n="profile.no_account">No account selected</div>
              <div class="line-me-sub" id="profileStatus">-</div>
            </div>
            <div class="line-me-pills">
              <span class="pill" id="authPill" data-i18n="pills.account">Account</span>
              <span class="pill" id="nodePill" data-i18n="pills.node">Node</span>
            </div>
          </div>
          <div class="line-me-detail kv advanced-only">
            <div data-i18n="profile.userid">User ID</div><div id="profileUserId">-</div>
            <div data-i18n="profile.e2ee">Encryption</div><div id="profileE2ee">-</div>
            <div data-i18n="profile.mid">Internal ID</div><div class="mono" id="profileMid">-</div>
          </div>
          <div class="line-list-tabbar">
            <div class="line-list-tabs subtabs" role="tablist" aria-label="Contacts and groups">
              <button id="loadContactsButton" class="subtab" type="button" role="tab" aria-selected="true" data-requires-account data-requires-permission="read" data-i18n="contacts.tab_people">Contacts</button>
              <button id="loadGroupsButton" class="subtab" type="button" role="tab" aria-selected="false" data-requires-account data-requires-permission="read" data-i18n="contacts.tab_groups">Groups</button>
            </div>
            <button id="contactsRefreshButton" class="icon-ghost" type="button" data-requires-account data-requires-permission="read" data-i18n-title="contacts.refresh_title" title="Refresh" aria-label="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-1.9 6.3"/><path d="M20 5v5h-5"/></svg></button>
          </div>
          <div class="line-search" id="contactSearchRow">
            <button id="contactSearchButton" class="search-icon" type="button" data-requires-account data-requires-permission="read" data-i18n-title="contacts.search" title="Search" aria-label="Search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6"/><path d="M21 21l-4-4"/></svg></button>
            <input id="contactSearch" data-requires-account data-i18n-ph="contacts.search_ph" placeholder="Search…">
          </div>
          <div class="line-rows contact-list" id="contactsList"></div>
          <div class="line-rows group-list hidden" id="groupsList"></div>
        </aside>

        <section class="line-convo">
          <header class="convo-header">
            <div class="convo-title">
              <h2 id="chatTitle" data-i18n="chat.title">Chat</h2>
              <span id="targetLabel" class="convo-sub" data-i18n="chat.no_target">No target selected</span>
            </div>
            <div class="convo-actions">
              <button class="icon-ghost" id="reloadMessagesButton" type="button" data-requires-account data-requires-permission="read" data-i18n-title="chat.refresh" title="Refresh" aria-label="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-1.9 6.3"/><path d="M20 5v5h-5"/></svg></button>
              <button class="icon-ghost" type="button" data-i18n-title="convo.mute" title="Mute" aria-label="Mute"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 19a2 2 0 0 0 4 0"/></svg></button>
              <button class="icon-ghost" type="button" data-i18n-title="convo.menu" title="Menu" aria-label="Menu"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
            </div>
          </header>
          <div class="convo-advanced advanced-only">
            <input id="targetInput" data-requires-account data-i18n-ph="chat.target_ph" placeholder="Target MID or contact name">
            <select id="messageCount" data-requires-account>
              <option value="20">20</option>
              <option value="50">50</option>
              <option value="100">100</option>
            </select>
            <button id="loadMessagesButton" data-requires-account data-requires-permission="read" data-i18n="chat.open">Open</button>
          </div>
          <div class="message-list line-messages" id="messagesList"></div>
          <div class="line-composer">
            <input type="file" id="imageFileInput" accept="image/*" hidden>
            <input type="file" id="fileFileInput" hidden>
            <div class="emoji-pop hidden" id="emojiPop"></div>
            <button class="icon-ghost" id="emojiButton" type="button" data-requires-account data-requires-permission="send" data-i18n-title="composer.emoji" title="Emoji" aria-label="Emoji"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/></svg></button>
            <button class="icon-ghost" id="attachButton" type="button" data-requires-account data-requires-permission="send" data-i18n-title="composer.attach" title="Attach" aria-label="Attach"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11l-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 6"/></svg></button>
            <button class="icon-ghost" id="imageButton" type="button" data-requires-account data-requires-permission="send" data-i18n-title="composer.image" title="Image" aria-label="Image"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M4 18l5-5 4 4 3-3 4 4"/></svg></button>
            <textarea id="messageText" rows="1" data-requires-account data-requires-permission="send" data-i18n-ph="chat.message_ph" placeholder="Message"></textarea>
            <button class="icon-toggle" id="sendEncryptedButton" type="button" aria-pressed="false" data-requires-account data-requires-permission="send" data-i18n-title="chat.send_encrypted" title="Send encrypted" aria-label="Send encrypted"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg></button>
            <button class="send-btn" id="sendButton" type="button" data-requires-account data-requires-permission="send" data-i18n-title="chat.send" title="Send" aria-label="Send"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4l17.4-8a1 1 0 0 0 0-1.8l-17.4-8A1 1 0 0 0 2 3.5L4 11l10 1-10 1-2 7.5a1 1 0 0 0 1.4 1z"/></svg></button>
          </div>
        </section>
          </div>

          <div class="tab-panel" data-tab-panel="bot" id="tabPanelBot" role="tabpanel" aria-labelledby="tabBot">
            <div class="tab-panel-inner">
          <div class="bot-shell">
          <div class="bot-page-nav" role="tablist" aria-label="Bot tools">
            <button type="button" id="botNavSchedules" data-bot-page-target="schedules" class="active" role="tab" aria-selected="true" data-i18n="bot.nav_schedules">Schedules</button>
            <button type="button" id="botNavScheduleForm" data-bot-page-target="schedule" role="tab" aria-selected="false" data-requires-account data-requires-permission="schedule" data-i18n="bot.nav_new">New schedule</button>
            <button type="button" id="botNavPatterns" data-bot-page-target="patterns" role="tab" aria-selected="false" data-requires-account data-requires-permission="schedule" data-i18n="bot.nav_patterns">Patterns</button>
            <button type="button" id="botNavLogs" data-bot-page-target="logs" role="tab" aria-selected="false" data-requires-account data-requires-permission="read" data-i18n="bot.nav_logs">Logs</button>
          </div>
          <div id="botScheduler" class="bot-page" data-bot-page="schedules">
          <section class="section">
            <div class="section-head">
              <h2 data-i18n="scheduler.title">Scheduler</h2>
              <div class="row compact">
                <span class="pill" id="scheduleCount">0</span>
                <button id="openBotLogsButton" type="button" data-requires-account data-requires-permission="read" data-i18n="bot.nav_logs">Logs</button>
                <button id="clearStuckSchedulesButton" type="button" class="danger hidden" data-requires-account data-requires-permission="schedule" data-i18n="scheduler.clear_stuck">Clear stuck jobs</button>
                <button id="openPatternsButton" type="button" data-requires-account data-requires-permission="schedule" data-i18n="scheduler.patterns_menu">Message patterns</button>
                <button id="scheduleFormToggle" class="primary" data-requires-account data-requires-permission="schedule" data-i18n="scheduler.new">+ New scheduled message</button>
              </div>
            </div>
            <div class="section-body">
              <div class="list schedule-list" id="schedulesList"></div>
            </div>
          </section>
          </div>
          <div id="botScheduleEditor" class="bot-page hidden" data-bot-page="schedule">
          <section class="section">
            <div class="section-head">
              <div class="row compact">
                <button id="scheduleBackButton" type="button" data-i18n="scheduler.back_schedules">← Back</button>
                <h2 id="scheduleEditorTitle" data-i18n="scheduler.new_title">New scheduled message</h2>
              </div>
            </div>
            <div class="section-body">
              <div class="form-grid" id="scheduleForm">
                <label>
                  <span data-i18n="scheduler.lbl_name">Job name</span>
                  <input id="scheduleName" data-requires-account data-i18n-ph="scheduler.name_ph" placeholder="Job name">
                </label>
                <select id="scheduleAccount" hidden aria-hidden="true"></select>
                <label>
                  <span data-i18n="scheduler.lbl_target">Send to</span>
                  <div class="subtabs" id="scheduleTargetTabs" role="tablist">
                    <button type="button" class="subtab active" data-ttab="people" role="tab" aria-selected="true" data-i18n="contacts.tab_people">Contacts</button>
                    <button type="button" class="subtab" data-ttab="groups" role="tab" aria-selected="false" data-i18n="contacts.tab_groups">Groups</button>
                  </div>
                  <select id="scheduleTarget" data-requires-account></select>
                </label>
                <label>
                  <span data-i18n="scheduler.lbl_source">Content type</span>
                  <select id="scheduleContentSource" data-requires-account>
                    <option value="text" data-i18n="scheduler.source_text">Text</option>
                    <option value="image" data-i18n="scheduler.source_image">Image URL / file</option>
                    <option value="ai_image" data-i18n="scheduler.source_ai_image">AI image</option>
                    <option value="api" data-i18n="scheduler.source_api">API text / image</option>
                  </select>
                </label>
                <label id="scheduleTextLabel">
                  <span data-i18n="scheduler.lbl_message">Message</span>
                  <textarea id="scheduleText" rows="4" data-requires-account data-i18n-ph="scheduler.message_ph" placeholder="Message to send"></textarea>
                </label>
                <label id="patternRow">
                  <span data-i18n="scheduler.lbl_patterns">Message patterns — tick to use (2+ → random each send)</span>
                  <div class="pattern-list" id="patternList"></div>
                </label>
                <div class="ph-help" id="scheduleTextHelp">
                  <span class="muted" data-i18n="scheduler.ph_hint">Insert (random on every send):</span>
                  <button type="button" class="ph-chip" data-ph="{1D}">{1D}</button>
                  <button type="button" class="ph-chip" data-ph="{2D}">{2D}</button>
                  <button type="button" class="ph-chip" data-ph="{3D}">{3D}</button>
                  <button type="button" class="ph-chip" data-ph="{date}">{date}</button>
                  <button type="button" class="ph-chip" data-ph="{time}">{time}</button>
                  <button type="button" class="ph-chip" data-ph="{rand:1-100}">{rand:1-100}</button>
                </div>
                <div class="ph-preview muted" id="scheduleTextPreview"></div>
                <label id="scheduleImageLabel" style="display:none">
                  <span data-i18n="scheduler.lbl_image">Image (URL / local path)</span>
                  <input id="scheduleImageSource" data-requires-account data-i18n-ph="scheduler.image_ph" placeholder="Image URL or local path">
                </label>
                <div class="row compact" id="scheduleImageUploadRow" style="display:none">
                  <input type="file" id="scheduleImageInput" accept="image/*" hidden>
                  <button type="button" id="scheduleImageUploadButton" data-i18n="scheduler.upload_image">Upload image…</button>
                  <span class="muted mono" id="scheduleImageName"></span>
                </div>
                <label id="aiPromptLabel" style="display:none">
                  <span data-i18n="scheduler.lbl_ai_prompt">AI image prompt</span>
                  <textarea id="scheduleAiPrompt" rows="3" data-requires-account data-i18n-ph="scheduler.ai_prompt_ph" placeholder="Describe the image for the bot to generate"></textarea>
                  <span class="muted" id="aiPromptHint" data-i18n="scheduler.ai_prompt_hint">The bot sends this prompt to the AI and posts the returned image — or tick message patterns below to randomise the prompt. Set your API key in the AI tab.</span>
                </label>
                <div class="form-grid" id="apiFields" style="display:none">
                  <div class="two grid">
                    <label>
                      <span data-i18n="scheduler.lbl_api_method">Method</span>
                      <select id="scheduleApiMethod" data-requires-account>
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                      </select>
                    </label>
                    <label>
                      <span data-i18n="scheduler.lbl_api_url">API URL</span>
                      <input id="scheduleApiUrl" data-requires-account data-i18n-ph="scheduler.api_url_ph" placeholder="API URL">
                    </label>
                  </div>
                  <label>
                    <span data-i18n="scheduler.lbl_api_body">POST body (JSON)</span>
                    <textarea id="scheduleApiBody" data-requires-account data-i18n-ph="scheduler.api_body_ph" placeholder="POST JSON body"></textarea>
                  </label>
                </div>
                <div class="two grid">
                  <label>
                    <span data-i18n="scheduler.lbl_mode">Mode</span>
                    <select id="scheduleMode" data-requires-account>
                      <option value="once" data-i18n="scheduler.mode_once">Send once</option>
                      <option value="repeat" data-i18n="scheduler.mode_repeat">Repeat</option>
                    </select>
                  </label>
                  <label>
                    <span data-i18n="scheduler.encrypt">Encryption</span>
                    <select id="scheduleEncrypt" data-requires-account>
                      <option value="false" data-i18n="scheduler.off">Off</option>
                      <option value="true" data-i18n="scheduler.on">On</option>
                    </select>
                  </label>
                </div>
                <label id="runAtWrap">
                  <span data-i18n="scheduler.send_at">Send at</span>
                  <div class="hm-pick">
                    <span class="date-field" style="flex:2">
                      <input type="text" id="scheduleRunAtDateText" class="date-text" readonly tabindex="-1" data-requires-account data-i18n-ph="scheduler.date_ph" placeholder="วว/ดด/ปปปป">
                      <input type="date" id="scheduleRunAtDate" class="date-native" data-requires-account aria-label="Send date">
                    </span>
                    <select id="scheduleRunAtH" data-requires-account></select>
                    <span>:</span>
                    <select id="scheduleRunAtM" data-requires-account></select>
                  </div>
                </label>
                <div class="form-grid" id="repeatFields" style="display:none">
                  <div class="two grid">
                    <label>
                      <span data-i18n="scheduler.window_start">Window start</span>
                      <div class="hm-pick">
                        <select id="scheduleWindowStartH" data-requires-account></select>
                        <span>:</span>
                        <select id="scheduleWindowStartM" data-requires-account></select>
                      </div>
                    </label>
                    <label>
                      <span data-i18n="scheduler.window_end">Window end</span>
                      <div class="hm-pick">
                        <select id="scheduleWindowEndH" data-requires-account></select>
                        <span>:</span>
                        <select id="scheduleWindowEndM" data-requires-account></select>
                      </div>
                    </label>
                  </div>
                  <div class="two grid">
                    <label>
                      <span data-i18n="scheduler.active_from">Active from</span>
                      <span class="date-field">
                        <input type="text" id="scheduleActiveFromText" class="date-text" readonly tabindex="-1" data-requires-account data-i18n-ph="scheduler.date_ph" placeholder="วว/ดด/ปปปป">
                        <input type="date" id="scheduleActiveFrom" class="date-native" data-requires-account aria-label="Active from">
                      </span>
                    </label>
                    <label>
                      <span data-i18n="scheduler.active_until">Active until</span>
                      <span class="date-field">
                        <input type="text" id="scheduleActiveUntilText" class="date-text" readonly tabindex="-1" data-requires-account data-i18n-ph="scheduler.date_ph" placeholder="วว/ดด/ปปปป">
                        <input type="date" id="scheduleActiveUntil" class="date-native" data-requires-account aria-label="Active until">
                      </span>
                    </label>
                  </div>
                  <div class="two grid">
                    <label>
                      <span data-i18n="scheduler.interval">Every (minutes)</span>
                      <input id="scheduleInterval" data-requires-account type="number" min="1" max="1440" value="15">
                    </label>
                    <label>
                      <span data-i18n="scheduler.max">Stop after N sends (blank = unlimited)</span>
                      <input id="scheduleMaxRuns" data-requires-account type="number" min="1" placeholder="∞">
                    </label>
                  </div>
                </div>
                <div class="schedule-summary" id="scheduleSummary"></div>
                <div class="row compact">
                  <button id="createScheduleButton" class="primary" data-requires-account data-requires-permission="schedule" data-i18n="scheduler.create">Create Schedule</button>
                  <button id="cancelScheduleFormButton" type="button" data-i18n="common.cancel">Cancel</button>
                </div>
              </div>
            </div>
          </section>
          </div>
          <div id="botLogs" class="bot-page hidden" data-bot-page="logs">
          <section class="section">
            <div class="section-head">
              <h2 data-i18n="botlog.title">Bot Log</h2>
              <div class="row compact">
                <span class="pill" id="botLogCount">0</span>
                <button id="botLogRefreshButton" type="button" data-requires-account data-requires-permission="read" data-i18n="botlog.refresh">Refresh</button>
                <button id="botLogClearButton" type="button" class="danger" data-requires-account data-requires-permission="schedule" data-i18n="botlog.clear">Clear</button>
              </div>
            </div>
            <div class="section-body">
              <div class="bot-terminal">
                <div class="bot-terminal-bar" aria-hidden="true">
                  <span class="bot-terminal-title"><span class="bot-terminal-prompt">&gt;_</span> linepassport/bot.log</span>
                  <span class="bot-terminal-live">LIVE</span>
                </div>
                <div class="bot-log-list" id="botLogList" role="log" aria-live="polite" aria-relevant="additions text" tabindex="0"></div>
              </div>
            </div>
          </section>
          </div>
          <div id="botPatterns" class="bot-page hidden" data-bot-page="patterns">
          <section class="section pattern-page-header">
            <div class="section-head">
              <div class="row compact">
                <button id="closePatternsButton" type="button" data-i18n="scheduler.back_schedules">← Back</button>
                <h2 data-i18n="scheduler.patterns_title">Message patterns</h2>
              </div>
              <button id="patternFormToggle" class="primary" aria-expanded="false" aria-controls="patternFormSection" data-requires-account data-requires-permission="schedule" data-i18n="scheduler.add_pattern_new">+ Add pattern</button>
            </div>
          </section>
          <section class="section pattern-filter-panel">
            <div class="section-body pattern-filter-row">
                <label>
                  <span data-i18n="patterns.category_filter">Category</span>
                  <select id="patternCategoryFilter"></select>
                </label>
                <button id="openPatternCategoriesButton" class="pattern-settings-button" type="button" data-requires-account data-requires-permission="schedule" data-i18n-title="patterns.settings" title="Category settings" aria-label="Category settings"><span aria-hidden="true">&#9881;</span></button>
            </div>
          </section>
            <div class="pattern-main-column">
              <section class="section hidden" id="patternFormSection">
                <div class="section-head">
                  <h3 data-i18n="patterns.create_title">Create pattern</h3>
                  <button id="cancelPatternFormButton" type="button" data-i18n="common.cancel">Cancel</button>
                </div>
                <div class="section-body">
                  <div class="form-grid" id="patternForm">
                    <label>
                      <span data-i18n="patterns.category">Category</span>
                      <select id="newPatternCategory" data-requires-account></select>
                    </label>
                    <label>
                      <span data-i18n="scheduler.lbl_pattern_name">Pattern name</span>
                      <input id="newPatternName" data-requires-account data-i18n-ph="scheduler.pattern_name_ph" placeholder="e.g. Promo A">
                    </label>
                    <label>
                      <span data-i18n="scheduler.lbl_pattern_text">Message text</span>
                      <textarea id="newPatternText" rows="4" data-requires-account data-i18n-ph="scheduler.message_ph" placeholder="Message to send"></textarea>
                    </label>
                    <div class="ph-help" id="patternTextHelp">
                      <span class="muted" data-i18n="scheduler.ph_hint">Insert (random on every send):</span>
                      <button type="button" class="ph-chip" data-ph="{1D}">{1D}</button>
                      <button type="button" class="ph-chip" data-ph="{2D}">{2D}</button>
                      <button type="button" class="ph-chip" data-ph="{3D}">{3D}</button>
                      <button type="button" class="ph-chip" data-ph="{date}">{date}</button>
                      <button type="button" class="ph-chip" data-ph="{time}">{time}</button>
                      <button type="button" class="ph-chip" data-ph="{rand:1-100}">{rand:1-100}</button>
                    </div>
                    <div class="row pattern-form-actions">
                      <button id="createPatternButton" class="primary" data-requires-account data-requires-permission="schedule" data-i18n="scheduler.add_pattern">Add pattern</button>
                    </div>
                  </div>
                </div>
              </section>
              <section class="section pattern-list-panel">
                <div class="section-head">
                  <h3 data-i18n="patterns.list_title">Patterns</h3>
                  <span class="pill" id="patternManageCount">0</span>
                </div>
                <div class="section-body">
                  <div class="list" id="patternManageList"></div>
                </div>
              </section>
            </div>
          </div>
          <div id="botPatternCategories" class="bot-page hidden" data-bot-page="pattern-categories">
            <section class="section pattern-page-header">
              <div class="section-head">
                <div>
                  <div class="row compact">
                    <button id="closePatternCategoriesButton" type="button" data-i18n="scheduler.back_schedules">← Back</button>
                    <h2 data-i18n="patterns.manage_title">Manage categories</h2>
                  </div>
                  <span class="muted" data-i18n="patterns.manage_hint">Add, edit, or delete pattern categories</span>
                </div>
                <button id="addPatternCategoryButton" class="primary" type="button" data-requires-account data-requires-permission="schedule" data-i18n="patterns.add_category">+ Add category</button>
              </div>
            </section>
            <section class="section pattern-category-list-panel">
              <div class="section-head">
                <h3 data-i18n="patterns.categories_title">Categories</h3>
                <span class="pill" id="patternCategoryCount">0</span>
              </div>
              <div class="section-body">
                <div class="list" id="patternCategoryManageList"></div>
              </div>
            </section>
          </div>
            </div>
          </div>
          </div>

          <div class="tab-panel" data-tab-panel="ai" id="tabPanelAi" role="tabpanel" aria-labelledby="tabAi">
            <div class="tab-panel-inner">
              <section class="section">
                <div class="section-head">
                  <h2 data-i18n="ai.title">AI image settings</h2>
                  <span class="pill" id="aiStatusPill" data-i18n="ai.status_off">Not configured</span>
                </div>
                <div class="section-body">
                  <p class="muted" data-i18n="ai.intro">Pick an AI provider and paste its API key so the bot can generate images from a text prompt. Keys are stored locally on this machine.</p>
                  <div class="form-grid">
                    <label>
                      <span data-i18n="ai.lbl_provider">Provider</span>
                      <select id="aiProvider" data-requires-permission="manage_accounts"></select>
                    </label>
                    <label>
                      <span data-i18n="ai.lbl_api_key">API key</span>
                      <div class="row compact">
                        <input id="aiApiKey" type="password" autocomplete="off" spellcheck="false" style="flex:1" data-requires-permission="manage_accounts" data-i18n-ph="ai.api_key_ph" placeholder="Paste your API key">
                        <button type="button" id="aiApiKeyToggle" data-i18n="ai.reveal">Show</button>
                      </div>
                      <span class="muted mono" id="aiApiKeyCurrent"></span>
                    </label>
                    <label>
                      <span id="aiModelLabel">Image model</span>
                      <select id="aiModel" data-requires-permission="manage_accounts"></select>
                      <span class="muted" id="aiModelPrice" aria-live="polite"></span>
                    </label>
                    <label id="aiAspectRatioRow" class="hidden">
                      <span data-i18n="ai.lbl_size">Image size</span>
                      <select id="aiAspectRatio" data-requires-permission="manage_accounts"></select>
                    </label>
                    <div class="row compact">
                      <button id="aiSaveButton" class="primary" data-requires-permission="manage_accounts" data-i18n="ai.save">Save</button>
                      <button id="aiClearButton" class="danger" data-requires-permission="manage_accounts" data-i18n="ai.clear">Remove key</button>
                    </div>
                    <p class="muted" id="aiKeyHint"></p>
                  </div>
                </div>
              </section>
              <section class="section">
                <div class="section-head">
                  <h2 data-i18n="ai.test_title">Try it</h2>
                </div>
                <div class="section-body">
                  <div class="form-grid">
                    <label>
                      <span data-i18n="ai.lbl_prompt">Prompt</span>
                      <textarea id="aiTestPrompt" rows="3" data-requires-permission="schedule" data-i18n-ph="ai.prompt_ph" placeholder="A cute cat astronaut, watercolor"></textarea>
                    </label>
                    <div class="row compact">
                      <button id="aiTestButton" class="primary" data-requires-permission="schedule" data-i18n="ai.generate">Generate image</button>
                      <span class="muted" id="aiTestStatus"></span>
                    </div>
                    <div class="ai-preview hidden" id="aiTestPreview"></div>
                  </div>
                </div>
              </section>
            </div>
          </div>

          <div class="tab-panel" data-tab-panel="tools" id="tabPanelTools" role="tabpanel" aria-labelledby="tabTools">
            <div class="tab-panel-inner">
          <section class="section">
            <div class="section-head">
              <h2 data-i18n="tools.title">Tools</h2>
              <span class="pill" data-i18n="tools.selected_only">Selected account only</span>
            </div>
            <div class="section-body">
              <div class="two grid">
                <input id="findUserInput" data-requires-account data-i18n-ph="tools.find_ph" placeholder="LINE ID">
                <button id="findUserButton" data-requires-account data-requires-permission="tools" data-i18n="tools.find">Find User</button>
              </div>
              <pre id="toolOutput">{}</pre>
              <div class="form-grid advanced-only">
                <div class="two grid">
                  <input id="endpointInput" data-requires-account data-i18n-ph="tools.endpoint_ph" placeholder="Endpoint key">
                  <input id="endpointArgs" data-requires-account data-i18n-ph="tools.args_ph" placeholder="JSON args array" value="[]">
                </div>
                <button id="callEndpointButton" data-requires-account data-requires-permission="tools" data-i18n="tools.call">Call Endpoint</button>
              </div>
            </div>
          </section>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>

  <div class="account-switch-overlay hidden" id="accountSwitchLoading" role="status" aria-live="polite" aria-hidden="true">
    <div class="account-switch-card">
      <span class="spinner" aria-hidden="true"></span>
      <strong data-i18n="common.loading">Loading...</strong>
      <span class="muted" data-i18n="accounts.switching">Switching LINE account...</span>
    </div>
  </div>

  <div class="modal-overlay hidden" id="modalOverlay">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <h3 id="modalTitle"></h3>
      <div id="modalBody"></div>
      <div class="modal-actions">
        <button id="modalCancel" data-i18n="common.cancel">Cancel</button>
        <button id="modalConfirm" class="primary" data-i18n="common.confirm">Confirm</button>
      </div>
    </div>
  </div>

  <div class="toast" id="toast" aria-live="polite">
    <span class="toast-text" id="toastText"></span>
    <button class="toast-close" id="toastClose" aria-label="Dismiss" hidden>&times;</button>
  </div>
  <script>
    // ---- i18n (Thai default) -------------------------------------------
    // Each key holds { th, en }.  t(key) resolves for the current language;
    // missing keys fall back to English, then to the key itself.
    const I18N = {
      "auth.secure": {th: "เข้าถึงอย่างปลอดภัย", en: "Secure access"},
      "auth.signin_label": {th: "เข้าสู่ระบบ LinePassport", en: "Sign in to LinePassport"},
      "auth.create_label": {th: "ตั้งรหัสผ่านสำหรับเปิด LinePassport", en: "Create admin user"},
      "auth.email": {th: "อีเมล", en: "Email"},
      "auth.login_id": {th: "อีเมล หรือ God login", en: "Email or God login"},
      "auth.displayname": {th: "ชื่อที่แสดง", en: "Display name"},
      "auth.password": {th: "รหัสผ่าน", en: "Password"},
      "auth.confirm": {th: "ยืนยันรหัสผ่าน", en: "Confirm password"},
      "auth.continue": {th: "ดำเนินการต่อ", en: "Continue"},
      "auth.signin": {th: "เข้าสู่ระบบ", en: "Sign In"},
      "auth.create_btn": {th: "สร้างรหัสผ่าน", en: "Create Admin"},
      "auth.register_label": {th: "\u0e2a\u0e21\u0e31\u0e04\u0e23\u0e43\u0e0a\u0e49\u0e07\u0e32\u0e19 LinePassport", en: "Register for LinePassport"},
      "auth.register": {th: "\u0e2a\u0e21\u0e31\u0e04\u0e23\u0e43\u0e0a\u0e49\u0e07\u0e32\u0e19", en: "Register"},
      "auth.back_to_signin": {th: "\u0e01\u0e25\u0e31\u0e1a\u0e44\u0e1b\u0e40\u0e02\u0e49\u0e32\u0e2a\u0e39\u0e48\u0e23\u0e30\u0e1a\u0e1a", en: "Back to sign in"},
      "auth.need_account": {th: "\u0e22\u0e31\u0e07\u0e44\u0e21\u0e48\u0e21\u0e35\u0e1a\u0e31\u0e0d\u0e0a\u0e35?", en: "Need an account?"},
      "auth.have_account": {th: "\u0e21\u0e35\u0e1a\u0e31\u0e0d\u0e0a\u0e35\u0e41\u0e25\u0e49\u0e27?", en: "Already have an account?"},
      "auth.hint_checking": {th: "กำลังตรวจสอบ…", en: "Checking web auth"},
      "auth.hint_signin": {th: "ใช้อีเมลและรหัสผ่าน LinePassport ของคุณ", en: "Use your LinePassport email and password."},
      "auth.hint_create": {th: "นี่คือรหัสผ่านใหม่สำหรับเปิด LinePassport (คนละอันกับรหัสผ่าน LINE) และกู้คืนไม่ได้ โปรดจดเก็บไว้", en: "This is a NEW LinePassport password (not your LINE password). It cannot be recovered, so keep it safe."},
      "auth.hint_register": {th: "สมัครด้วยอีเมลและเริ่มใช้งานในพื้นที่ส่วนตัว จากนั้นเพิ่ม LINE Account และตั้งค่าทุกอย่างได้เอง", en: "Register with email to get a private workspace, then add your own LINE accounts and settings."},
      "auth.email_invalid": {th: "กรอกอีเมลให้ถูกต้อง", en: "Enter a valid email address."},
      "auth.offline": {th: "ติดต่อเซิร์ฟเวอร์ไม่ได้ — กำลังลองใหม่…", en: "Cannot reach the server — retrying…"},
      "auth.password_mismatch": {th: "รหัสผ่านไม่ตรงกัน", en: "Passwords do not match."},
      "topbar.home": {th: "\u0e01\u0e25\u0e31\u0e1a\u0e2b\u0e19\u0e49\u0e32\u0e41\u0e23\u0e01", en: "Home"},
      "topbar.account": {th: "บัญชี LINE", en: "LINE Account"},
      "topbar.refresh": {th: "รีเฟรช", en: "Refresh"},
      "topbar.settings": {th: "ตั้งค่า", en: "Settings"},
      "topbar.change_password": {th: "เปลี่ยนรหัสผ่าน", en: "Change Password"},
      "topbar.account_management": {th: "จัดการบัญชี", en: "Account Management"},
      "topbar.line_account_management": {th: "จัดการบัญชี LINE", en: "LINE Accounts"},
      "topbar.user_management": {th: "จัดการผู้ใช้", en: "Users"},
      "topbar.logout": {th: "ออกจากระบบ", en: "Log out"},
      "tabs.line": {th: "LINE", en: "LINE"},
      "tabs.tools": {th: "เครื่องมือ", en: "Tools"},
      "tabs.bot": {th: "บอท", en: "Bot"},
      "tabs.ai": {th: "AI", en: "AI Settings"},
      "ai.title": {th: "ตั้งค่า AI สร้างรูป", en: "AI image settings"},
      "ai.status_on": {th: "ตั้งค่าแล้ว", en: "Configured"},
      "ai.status_off": {th: "ยังไม่ได้ตั้งค่า", en: "Not configured"},
      "ai.intro": {th: "เลือกผู้ให้บริการ AI แล้ววาง API Key เพื่อให้บอทสร้างรูปภาพจากข้อความ คีย์จะถูกเก็บไว้ในเครื่องนี้เท่านั้น", en: "Pick an AI provider and paste its API key so the bot can generate images from a text prompt. Keys are stored locally on this machine."},
      "ai.lbl_provider": {th: "ผู้ให้บริการ", en: "Provider"},
      "ai.lbl_api_key": {th: "API Key", en: "API key"},
      "ai.api_key_ph": {th: "วาง API Key ที่นี่", en: "Paste your API key"},
      "ai.reveal": {th: "แสดง", en: "Show"},
      "ai.hide": {th: "ซ่อน", en: "Hide"},
      "ai.lbl_model": {th: "โมเดลรูปภาพ", en: "Image model"},
      "ai.price_note": {th: "ราคาโดยประมาณจาก fal.ai และอาจเปลี่ยนแปลงได้", en: "Estimated fal.ai price; actual billing may change"},
      "ai.lbl_size": {th: "สัดส่วนภาพ", en: "Image size"},
      "ai.size_auto": {th: "อัตโนมัติ", en: "Auto"},
      "ai.save": {th: "บันทึก", en: "Save"},
      "ai.clear": {th: "ลบคีย์", en: "Remove key"},
      "ai.hint_google": {th: "รับคีย์ที่ Google AI Studio (aistudio.google.com) → API keys — การสร้างรูปมักต้องเปิดการเรียกเก็บเงิน (billing)", en: "Get a key from Google AI Studio (aistudio.google.com) → API keys. Image generation usually needs billing enabled."},
      "ai.hint_nano": {th: "รับคีย์ที่ nanobananaapi.ai → API keys (ต้องมีเครดิตคงเหลือ) การสร้างรูปอาจใช้เวลาสองสามวินาที", en: "Get a key from nanobananaapi.ai → API keys (needs available credits). Generation may take a few seconds."},
      "ai.hint_fal": {th: "รับคีย์แบบ API scope ที่ fal.ai/dashboard/keys ระบบจะส่งงานผ่าน Queue API และดาวน์โหลดรูปเมื่อสร้างเสร็จ", en: "Create an API-scoped key at fal.ai/dashboard/keys. Jobs use the Queue API and download the image when ready."},
      "ai.get_key_hint": {th: "รับคีย์ได้ที่ Google AI Studio (aistudio.google.com) → API keys", en: "Get a key from Google AI Studio (aistudio.google.com) → API keys."},
      "ai.test_title": {th: "ลองใช้งาน", en: "Try it"},
      "ai.lbl_prompt": {th: "คำสั่ง (Prompt)", en: "Prompt"},
      "ai.prompt_ph": {th: "เช่น แมวนักบินอวกาศน่ารัก สไตล์สีน้ำ", en: "A cute cat astronaut, watercolor"},
      "ai.generate": {th: "สร้างรูปภาพ", en: "Generate image"},
      "ai.generating": {th: "กำลังสร้างรูป…", en: "Generating…"},
      "ai.done": {th: "เสร็จแล้ว", en: "Done"},
      "ai.current_key": {th: "คีย์ปัจจุบัน:", en: "Current key:"},
      "ai.no_key": {th: "ยังไม่ได้ตั้งค่าคีย์", en: "No key set yet."},
      "menu.language": {th: "ภาษา", en: "Language"},
      "convo.mute": {th: "ปิดเสียง", en: "Mute"},
      "convo.menu": {th: "เมนู", en: "Menu"},
      "composer.emoji": {th: "อีโมจิ", en: "Emoji"},
      "composer.attach": {th: "แนบไฟล์", en: "Attach"},
      "composer.image": {th: "รูปภาพ", en: "Image"},
      "role.god": {th: "God", en: "God"},
      "role.admin": {th: "แอดมิน", en: "Admin"},
      "role.member": {th: "สมาชิก", en: "Member"},
      "role.operator": {th: "ผู้ดูแล", en: "Operator"},
      "role.viewer": {th: "ผู้ชม", en: "Viewer"},
      "role.locked": {th: "ล็อกอยู่", en: "Locked"},
      "perm.denied": {th: "คุณไม่มีสิทธิ์ทำรายการนี้", en: "You do not have permission for this action."},
      "gate.title": {th: "เลือกบัญชี LINE", en: "Select a LINE account"},
      "gate.body": {th: "เลือกบัญชีจากเมนูด้านบนก่อนใช้งาน รายชื่อ แชท ส่งข้อความ หรือตัวตั้งเวลาส่ง", en: "Choose an account from the dropdown above before using Contacts, Chat, Send, or Scheduler."},
      "gate.none_shared_title": {th: "ยังไม่มีบัญชี LINE ที่แชร์ให้คุณ", en: "No LINE account shared with you"},
      "gate.none_shared_body": {th: "ขอสิทธิ์เข้าถึงบัญชี LINE จากแอดมิน", en: "Ask an administrator to grant you access to a LINE account."},
      "settings.title": {th: "ตั้งค่า", en: "Settings"},
      "settings.subtitle": {th: "จัดการการเข้าถึงและบัญชี LINE", en: "Manage access and LINE accounts."},
      "settings.subtitle_password": {th: "เปลี่ยนรหัสผ่าน", en: "Change Password"},
      "settings.subtitle_accounts": {th: "จัดการบัญชี", en: "Account Management"},
      "settings.subtitle_line_accounts": {th: "เพิ่ม แก้ไข หรือลบบัญชี LINE", en: "Add, edit, or delete LINE accounts."},
      "settings.subtitle_users": {th: "จัดการผู้ใช้ บทบาท และบัญชี LINE ที่เข้าถึงได้", en: "Manage users, roles, and LINE account access."},
      "settings.back": {th: "ย้อนกลับ", en: "Back"},
      "password.title": {th: "เปลี่ยนรหัสผ่าน", en: "Change Password"},
      "password.current": {th: "รหัสผ่านปัจจุบัน", en: "Current password"},
      "password.new": {th: "รหัสผ่านใหม่", en: "New password"},
      "password.update": {th: "อัปเดตรหัสผ่าน", en: "Update Password"},
      "accounts.title": {th: "บัญชี LINE", en: "LINE Accounts"},
      "accounts.subtitle": {th: "เพิ่ม แก้ไข หรือลบเซสชัน LINE ในเครื่อง", en: "Add, edit, or delete local LINE sessions."},
      "accounts.add": {th: "เพิ่มบัญชี", en: "Add Account"},
      "accounts.select": {th: "เลือกบัญชี LINE", en: "Select LINE account"},
      "accounts.switching": {th: "กำลังสลับบัญชี LINE...", en: "Switching LINE account..."},
      "accounts.none_yet": {th: "ยังไม่มีบัญชี", en: "No accounts yet"},
      "accounts.none": {th: "ไม่มีบัญชี LINE", en: "No LINE accounts"},
      "accounts.missing": {th: "เซสชันหาย", en: "missing"},
      "accounts.session_ready": {th: "เซสชันพร้อม", en: "Session ready"},
      "accounts.session_missing": {th: "เซสชันหาย", en: "Session missing"},
      "accounts.use": {th: "ใช้บัญชีนี้", en: "Use"},
      "accounts.edit_title": {th: "เปลี่ยนชื่อบัญชี", en: "Rename account"},
      "accounts.name": {th: "ชื่อบัญชี LINE", en: "LINE account name"},
      "accounts.delete": {th: "ลบ", en: "Delete"},
      "accounts.delete_title": {th: "ลบ {name}?", en: "Delete {name}?"},
      "accounts.delete_note": {th: "งานตั้งเวลาส่งของบัญชีนี้จะถูกหยุดชั่วคราว", en: "This account's scheduled jobs will be paused."},
      "accounts.also_line_logout": {th: "ออกจากระบบบน LINE ด้วย", en: "Also log out on LINE"},
      "users.title": {th: "ผู้ใช้และสิทธิ์", en: "Users & Access"},
      "users.subtitle": {th: "กำหนดบทบาทและเลือกบัญชี LINE ที่ผู้ใช้แต่ละคนเข้าถึงได้", en: "Assign roles and choose which LINE accounts each user can access."},
      "users.create": {th: "สร้างผู้ใช้", en: "Create User"},
      "users.email": {th: "อีเมล", en: "Email"},
      "users.displayname": {th: "ชื่อที่แสดง", en: "Display name"},
      "users.password": {th: "รหัสผ่าน", en: "Password"},
      "users.new_password_optional": {th: "รหัสผ่านใหม่ (เว้นว่างหากไม่เปลี่ยน)", en: "New password (leave blank to keep)"},
      "users.role": {th: "บทบาท", en: "Role"},
      "users.accounts": {th: "บัญชี LINE ที่เข้าถึงได้", en: "Accessible LINE accounts"},
      "users.all_accounts": {th: "ทุกบัญชี LINE", en: "All LINE accounts"},
      "users.no_accounts": {th: "ไม่มีบัญชี LINE", en: "No LINE accounts"},
      "users.admin_all_note": {th: "เฉพาะ God เท่านั้นที่เข้าถึงทุกบัญชี ผู้ใช้อื่นเห็นเฉพาะบัญชีของตนเอง", en: "Only God can access every account; other users see only their own accounts."},
      "users.disabled": {th: "ปิดใช้งาน", en: "disabled"},
      "users.god_protected": {th: "บัญชี God จัดการได้โดย God เท่านั้น", en: "Only God can manage this account"},
      "users.details": {th: "รายละเอียด", en: "Details"},
      "users.detail_line": {th: "บัญชี LINE", en: "LINE accounts"},
      "users.detail_patterns": {th: "แพทเทิร์น", en: "Patterns"},
      "users.detail_schedules": {th: "งานบอท", en: "Bot schedules"},
      "users.detail_ai": {th: "AI ที่ใช้งาน", en: "AI configuration"},
      "users.detail_empty": {th: "ไม่มีข้อมูล", en: "No data"},
      "users.ai_not_configured": {th: "ยังไม่ตั้ง API key", en: "API key not configured"},
      "users.ai_configured": {th: "ตั้ง API key แล้ว", en: "API key configured"},
      "users.disable": {th: "ปิดใช้งาน", en: "Disable"},
      "users.enable": {th: "เปิดใช้งาน", en: "Enable"},
      "users.none": {th: "ยังไม่มีผู้ใช้", en: "No users"},
      "secure.title": {th: "ตั้งรหัสผ่านเพื่อเปิด LinePassport", en: "Require a password to open LinePassport"},
      "secure.body": {th: "ตอนนี้ LinePassport เปิดได้โดยไม่ต้องเข้าสู่ระบบ ตั้งรหัสผ่านเว็บ (คนละอันกับ LINE และกู้คืนไม่ได้) เพื่อบังคับให้ต้องเข้าสู่ระบบ", en: "LinePassport currently opens without a login. Set a web password (separate from LINE, and not recoverable) to require sign-in."},
      "secure.action": {th: "ตั้งรหัสผ่าน", en: "Set a password"},
      "login.add_title": {th: "เพิ่มบัญชี LINE", en: "Add LINE Account"},
      "login.subtitle": {th: "ทำตาม 4 ขั้นตอนเพื่อเพิ่มเซสชัน LINE — LinePassport จะใช้ชื่อที่แสดงจากบัญชี LINE ของคุณ", en: "Follow the 4 steps to add another LINE session. LinePassport will use the display name from your LINE account."},
      "login.back": {th: "ย้อนกลับ", en: "Back"},
      "login.step_1": {th: "ขั้นตอน 1", en: "Step 1"},
      "login.step_2": {th: "ขั้นตอน 2", en: "Step 2"},
      "login.step_3": {th: "ขั้นตอน 3", en: "Step 3"},
      "login.step_4": {th: "ขั้นตอน 4", en: "Step 4"},
      "login.step1": {th: "เริ่ม", en: "Start"},
      "login.step2": {th: "สแกน QR", en: "Scan QR"},
      "login.step3": {th: "ยืนยัน PIN", en: "Confirm PIN"},
      "login.step4": {th: "เสร็จสิ้น", en: "Done"},
      "login.step1of4": {th: "ขั้นตอนที่ 1 จาก 4", en: "Step 1 of 4"},
      "login.step2of4": {th: "ขั้นตอนที่ 2 จาก 4", en: "Step 2 of 4"},
      "login.step3of4": {th: "ขั้นตอนที่ 3 จาก 4", en: "Step 3 of 4"},
      "login.step4of4": {th: "ขั้นตอนที่ 4 จาก 4", en: "Step 4 of 4"},
      "login.start_title": {th: "เริ่มเพิ่มบัญชี LINE", en: "Start adding a LINE account"},
      "login.start_hint": {th: "เปิดขั้นตอนเข้าสู่ระบบใหม่ LinePassport จะใช้ชื่อที่แสดงจากบัญชี LINE ของคุณ", en: "This opens a clean login flow. LinePassport will use the display name from your LINE account."},
      "login.start": {th: "เริ่ม", en: "Start"},
      "login.starting": {th: "กำลังเริ่ม…", en: "Starting"},
      "login.scan_title": {th: "สแกน QR code", en: "Scan the QR code"},
      "login.qr_placeholder": {th: "QR จะปรากฏที่นี่", en: "QR will appear here"},
      "login.qr_instructions": {th: "เปิด LINE บนมือถือ ไปที่การเพิ่มเพื่อน แล้วสแกน หรือคัดลอกลิงก์ด้านล่างไปเปิดในแอป LINE", en: "Open LINE on your phone, go to add-friend, then scan — or copy the link below and open it in the LINE app."},
      "login.pin_title": {th: "ยืนยัน PIN", en: "Confirm PIN"},
      "login.pin_hint": {th: "หาก LINE ขอ PIN ให้ยืนยันตัวเลขนี้บนอุปกรณ์ LINE ของคุณ", en: "If LINE asks for a PIN, confirm this number on your LINE device."},
      "login.done_title": {th: "กำลังเปิดบัญชี", en: "Opening account"},
      "login.done_text": {th: "หลังเข้าสู่ระบบเสร็จ LinePassport จะเปิดบัญชี LINE นี้ให้อัตโนมัติ", en: "After login finishes, LinePassport will open this LINE account automatically."},
      "login.added_opening": {th: "เพิ่ม {name} สำเร็จ กำลังเปิดบัญชีนี้…", en: "{name} added successfully. Opening this account…"},
      "login.line_account": {th: "บัญชี LINE", en: "LINE account"},
      "login.idle": {th: "พร้อม", en: "Idle"},
      "login.state.starting": {th: "กำลังเริ่ม…", en: "Starting…"},
      "login.state.qr": {th: "รอสแกน QR", en: "Waiting for QR scan"},
      "login.state.pin": {th: "รอยืนยัน PIN", en: "Waiting for PIN"},
      "login.state.success": {th: "สำเร็จ", en: "Success"},
      "login.state.error": {th: "ผิดพลาด", en: "Error"},
      "login.state.idle": {th: "พร้อม", en: "Idle"},
      "profile.no_account": {th: "ยังไม่ได้เลือกบัญชี", en: "No account selected"},
      "profile.selected": {th: "บัญชีที่เลือก", en: "Selected account"},
      "profile.mid": {th: "รหัสภายใน", en: "Internal ID"},
      "profile.userid": {th: "User ID", en: "User ID"},
      "profile.status": {th: "สถานะ", en: "Status"},
      "profile.e2ee": {th: "การเข้ารหัส", en: "Encryption"},
      "profile.e2ee_ready": {th: "พร้อม", en: "ready"},
      "profile.e2ee_off": {th: "ปิด", en: "off"},
      "pills.account": {th: "บัญชี", en: "Account"},
      "pills.select_account": {th: "เลือกบัญชี", en: "Select account"},
      "pills.node": {th: "Node", en: "Node"},
      "pills.node_ready": {th: "พร้อมส่งข้อความ", en: "Ready to send"},
      "pills.node_missing": {th: "ต้องติดตั้ง Node.js เพื่อส่งข้อความ", en: "Install Node.js to send messages"},
      "pills.connected": {th: "เชื่อมต่อแล้ว", en: "Connected"},
      "pills.not_connected": {th: "ยังไม่เชื่อมต่อ", en: "Not connected"},
      "contacts.title": {th: "รายชื่อ", en: "Contacts"},
      "contacts.groups": {th: "กลุ่ม", en: "Groups"},
      "contacts.refresh": {th: "รีเฟรช", en: "Refresh"},
      "contacts.tab_people": {th: "รายชื่อ", en: "Contacts"},
      "contacts.tab_groups": {th: "กลุ่ม", en: "Groups"},
      "contacts.refresh_title": {th: "รีเฟรช", en: "Refresh"},
      "contacts.search": {th: "ค้นหา", en: "Search"},
      "contacts.search_ph": {th: "ค้นหา…", en: "Search…"},
      "contacts.none": {th: "ไม่มีรายชื่อ", en: "No contacts"},
      "contacts.no_match": {th: "ไม่พบรายชื่อที่ค้นหา", en: "No matching contacts"},
      "groups.none": {th: "ไม่มีกลุ่ม", en: "No groups"},
      "groups.members": {th: "{n} สมาชิก", en: "{n} members"},
      "chat.title": {th: "แชท", en: "Chat"},
      "chat.no_target": {th: "ยังไม่ได้เลือกปลายทาง", en: "No target selected"},
      "chat.target_ph": {th: "รหัสภายใน (MID) หรือชื่อผู้ติดต่อ", en: "Target MID or contact name"},
      "chat.open": {th: "เปิด", en: "Open"},
      "chat.refresh": {th: "รีเฟรช", en: "Refresh"},
      "chat.message_ph": {th: "พิมพ์ข้อความ", en: "Message"},
      "chat.send": {th: "ส่งข้อความ", en: "Send"},
      "chat.send_encrypted": {th: "ส่งแบบเข้ารหัส 🔒", en: "Send encrypted 🔒"},
      "chat.e2ee_disabled_tip": {th: "บัญชีนี้ยังไม่พร้อมเข้ารหัส (E2EE)", en: "Encryption (E2EE) is not ready for this account"},
      "chat.empty": {th: "ยังไม่มีข้อความ", en: "No messages"},
      "chat.unknown": {th: "ไม่ทราบ", en: "unknown"},
      "chat.sealed_image": {th: "🔒 รูปภาพ (เข้ารหัส — เปิดในแอป LINE)", en: "🔒 Image (encrypted — open in LINE)"},
      "chat.sealed_file": {th: "🔒 ไฟล์ (เข้ารหัส — เปิดในแอป LINE)", en: "🔒 File (encrypted — open in LINE)"},
      "chat.today": {th: "วันนี้", en: "Today"},
      "chat.yesterday": {th: "เมื่อวาน", en: "Yesterday"},
      "content.image": {th: "[รูปภาพ]", en: "[image]"},
      "content.video": {th: "[วิดีโอ]", en: "[video]"},
      "content.audio": {th: "[เสียง]", en: "[audio]"},
      "content.sticker": {th: "[สติกเกอร์]", en: "[sticker]"},
      "content.file": {th: "[ไฟล์]", en: "[file]"},
      "content.unsupported": {th: "[ข้อความที่ไม่รองรับ]", en: "[unsupported message]"},
      "scheduler.title": {th: "ตัวตั้งเวลาส่ง / Scheduler", en: "Scheduler"},
      "bot.nav_schedules": {th: "รายการบอท", en: "Schedules"},
      "bot.nav_new": {th: "ตั้งเวลาส่งใหม่", en: "New schedule"},
      "bot.nav_patterns": {th: "แพทเทิร์น", en: "Patterns"},
      "bot.nav_logs": {th: "Log", en: "Logs"},
      "scheduler.jobs": {th: "{n} งาน", en: "{n} jobs"},
      "scheduler.new": {th: "+ ตั้งเวลาส่งใหม่", en: "+ New scheduled message"},
      "scheduler.name_ph": {th: "ชื่องาน", en: "Job name"},
      "scheduler.target_ph": {th: "รหัสภายใน (MID) หรือชื่อผู้ติดต่อ", en: "Target MID or contact name"},
      "scheduler.source_text": {th: "ข้อความ", en: "Text"},
      "scheduler.source_image": {th: "รูปภาพ (URL/ไฟล์)", en: "Image URL / file"},
      "scheduler.source_ai_image": {th: "รูปภาพจาก AI", en: "AI image"},
      "scheduler.source_api": {th: "API (ข้อความ/รูป)", en: "API text / image"},
      "scheduler.message_ph": {th: "ข้อความที่จะส่ง", en: "Message to send"},
      "scheduler.image_ph": {th: "URL รูปภาพหรือพาธไฟล์", en: "Image URL or local path"},
      "scheduler.api_url_ph": {th: "URL ของ API", en: "API URL"},
      "scheduler.api_body_ph": {th: "เนื้อหา JSON สำหรับ POST", en: "POST JSON body"},
      "scheduler.date_ph": {th: "วว/ดด/ปปปป", en: "dd/mm/yyyy"},
      "scheduler.mode_once": {th: "ส่งครั้งเดียว", en: "Send once"},
      "scheduler.mode_repeat": {th: "ส่งซ้ำ", en: "Repeat"},
      "scheduler.encrypt": {th: "การเข้ารหัส", en: "Encryption"},
      "scheduler.on": {th: "เปิด", en: "On"},
      "scheduler.off": {th: "ปิด", en: "Off"},
      "scheduler.send_at": {th: "ส่งเมื่อ", en: "Send at"},
      "scheduler.window_start": {th: "เริ่มช่วงเวลา", en: "Window start"},
      "scheduler.window_end": {th: "สิ้นสุดช่วงเวลา", en: "Window end"},
      "scheduler.active_from": {th: "เริ่มใช้งานวันที่", en: "Active from"},
      "scheduler.active_until": {th: "ใช้งานถึงวันที่", en: "Active until"},
      "scheduler.interval": {th: "ส่งทุกกี่นาที", en: "Every (minutes)"},
      "scheduler.ph_hint": {th: "แทรก (สุ่มใหม่ทุกครั้งที่ส่ง):", en: "Insert (random on every send):"},
      "scheduler.upload_image": {th: "อัปโหลดรูป…", en: "Upload image…"},
      "scheduler.preview": {th: "ตัวอย่าง:", en: "Preview:"},
      "scheduler.max": {th: "หยุดหลังส่ง N ครั้ง (เว้นว่าง = ไม่จำกัด)", en: "Stop after N sends (blank = unlimited)"},
      "scheduler.create": {th: "สร้างตารางเวลา", en: "Create Schedule"},
      "scheduler.new_title": {th: "ตั้งเวลาส่งใหม่", en: "New scheduled message"},
      "scheduler.edit_title": {th: "แก้ไขตารางเวลา", en: "Edit scheduled message"},
      "scheduler.save_edit": {th: "บันทึกการแก้ไข", en: "Save changes"},
      "scheduler.edit": {th: "แก้ไข", en: "Edit"},
      "scheduler.pick_target": {th: "— เลือกผู้รับ —", en: "— Select recipient —"},
      "scheduler.pick_pattern": {th: "— เลือกแพทเทิร์นข้อความ —", en: "— Select message pattern —"},
      "scheduler.save_pattern": {th: "บันทึกเป็นแพทเทิร์น", en: "Save as pattern"},
      "scheduler.del_pattern": {th: "ลบแพทเทิร์นที่เลือก", en: "Delete selected pattern"},
      "scheduler.pattern_name_prompt": {th: "ตั้งชื่อแพทเทิร์น", en: "Pattern name"},
      "scheduler.no_patterns": {th: "ยังไม่มีแพทเทิร์น — เพิ่มที่ปุ่ม ‘แพทเทิร์นข้อความ’ ด้านบน", en: "No patterns — add via the ‘Message patterns’ button above"},
      "scheduler.no_patterns_manage": {th: "ยังไม่มีแพทเทิร์น เพิ่มด้านบนได้เลย", en: "No patterns yet — add one above"},
      "scheduler.patterns_menu": {th: "แพทเทิร์นข้อความ", en: "Message patterns"},
      "scheduler.patterns_title": {th: "จัดการแพทเทิร์นข้อความ", en: "Manage message patterns"},
      "scheduler.lbl_pattern_name": {th: "ชื่อแพทเทิร์น", en: "Pattern name"},
      "scheduler.lbl_pattern_text": {th: "ข้อความ", en: "Message text"},
      "scheduler.pattern_name_ph": {th: "เช่น โปรโมชั่น A", en: "e.g. Promo A"},
      "scheduler.add_pattern": {th: "เพิ่มแพทเทิร์น", en: "Add pattern"},
      "scheduler.add_pattern_new": {th: "+ เพิ่มแพทเทิร์น", en: "+ Add pattern"},
      "scheduler.back_schedules": {th: "← กลับ", en: "← Back"},
      "toast.pattern_name_required": {th: "ต้องตั้งชื่อแพทเทิร์น", en: "Pattern name is required"},
      "common.close": {th: "ปิด", en: "Close"},
      "scheduler.lbl_name": {th: "ชื่องาน", en: "Job name"},
      "scheduler.lbl_target": {th: "ส่งถึงใคร", en: "Send to"},
      "scheduler.lbl_source": {th: "ประเภทเนื้อหา", en: "Content type"},
      "scheduler.lbl_message": {th: "ข้อความ", en: "Message"},
      "scheduler.lbl_patterns": {th: "แพทเทิร์นข้อความ — ติ๊กเพื่อใช้ (เลือก 2+ ระบบสุ่มให้ทุกครั้ง)", en: "Message patterns — tick to use (2+ → random each send)"},
      "scheduler.lbl_image": {th: "รูป (URL / พาธ)", en: "Image (URL / path)"},
      "scheduler.lbl_mode": {th: "โหมด", en: "Mode"},
      "scheduler.lbl_api_method": {th: "เมธอด", en: "Method"},
      "scheduler.lbl_api_url": {th: "API URL", en: "API URL"},
      "scheduler.lbl_api_body": {th: "POST body (JSON)", en: "POST body (JSON)"},
      "scheduler.lbl_ai_prompt": {th: "คำสั่งสร้างรูปด้วย AI", en: "AI image prompt"},
      "scheduler.ai_prompt_ph": {th: "อธิบายรูปที่จะให้บอทสร้าง", en: "Describe the image for the bot to generate"},
      "scheduler.ai_prompt_hint": {th: "บอทจะส่งคำสั่งนี้ไปให้ AI แล้วส่งรูปที่ได้กลับมา หรือจะติ๊กแพทเทิร์นข้อความด้านล่างเพื่อสุ่มคำสั่งก็ได้ — ตั้งค่า API Key ที่แท็บ AI", en: "The bot sends this prompt to the AI and posts the returned image — or tick message patterns below to randomise the prompt. Set your API key in the AI tab."},
      "toast.pattern_saved": {th: "บันทึกแพทเทิร์นแล้ว", en: "Pattern saved"},
      "toast.pattern_deleted": {th: "ลบแพทเทิร์นแล้ว", en: "Pattern deleted"},
      "confirm.delete_pattern": {th: "ลบแพทเทิร์นนี้?", en: "Delete this pattern?"},
      "scheduler.none": {th: "ยังไม่มีตารางเวลาสำหรับบัญชีนี้", en: "No schedules for this account"},
      "scheduler.default_name": {th: "ข้อความตามเวลา", en: "Scheduled message"},
      "scheduler.pause": {th: "หยุด", en: "Pause"},
      "scheduler.resume": {th: "ทำต่อ", en: "Resume"},
      "scheduler.run_now": {th: "ส่งเดี๋ยวนี้", en: "Run now"},
      "scheduler.delete": {th: "ลบ", en: "Delete"},
      "scheduler.enabled": {th: "ทำงานอยู่", en: "enabled"},
      "scheduler.paused": {th: "หยุดชั่วคราว", en: "paused"},
      "scheduler.running": {th: "กำลังทำงาน", en: "running"},
      "scheduler.clear_stuck": {th: "เคลียร์งานค้าง", en: "Clear stuck jobs"},
      "scheduler.clear_stuck_count": {th: "เคลียร์งานค้าง ({n})", en: "Clear stuck jobs ({n})"},
      "scheduler.clear_stuck_title": {th: "เคลียร์งานค้าง?", en: "Clear stuck jobs?"},
      "scheduler.clear_stuck_note": {th: "ระบบจะปลดสถานะกำลังทำงาน/ผิดพลาดของ {n} งานในบัญชี LINE ที่เลือก โดยไม่ลบตารางเวลา", en: "This resets the running/error state for {n} job(s) in the selected LINE account without deleting schedules."},
      "scheduler.next": {th: "ครั้งถัดไป", en: "next"},
      "scheduler.sent": {th: "ส่งแล้ว", en: "sent"},
      "scheduler.error": {th: "ข้อผิดพลาด", en: "error"},
      "scheduler.summary_once": {th: "จะส่งครั้งเดียว วันที่ {at}", en: "Sends once at {at}"},
      "scheduler.summary_once_empty": {th: "เลือกวันและเวลาที่จะส่ง", en: "Pick a date and time to send."},
      "scheduler.summary_repeat": {th: "ส่งซ้ำทุก {interval} นาที ระหว่าง {start}–{end}", en: "Repeats every {interval} min between {start}–{end}"},
      "scheduler.summary_max": {th: "หยุดหลังส่ง {n} ครั้ง", en: "Stops after {n} sends"},
      "tools.title": {th: "เครื่องมือ", en: "Tools"},
      "tools.selected_only": {th: "เฉพาะบัญชีที่เลือก", en: "Selected account only"},
      "tools.find_ph": {th: "LINE ID", en: "LINE ID"},
      "tools.find": {th: "ค้นหาผู้ใช้", en: "Find User"},
      "tools.endpoint_ph": {th: "ชื่อ Endpoint", en: "Endpoint key"},
      "tools.args_ph": {th: "อาร์กิวเมนต์แบบ JSON array", en: "JSON args array"},
      "tools.call": {th: "เรียก Endpoint", en: "Call Endpoint"},
      "common.confirm": {th: "ยืนยัน", en: "Confirm"},
      "common.cancel": {th: "ยกเลิก", en: "Cancel"},
      "common.add": {th: "เพิ่ม", en: "Add"},
      "common.save": {th: "บันทึก", en: "Save"},
      "common.edit": {th: "แก้ไข", en: "Edit"},
      "common.delete": {th: "ลบ", en: "Delete"},
      "common.loading": {th: "กำลังโหลด…", en: "Loading…"},
      "common.working": {th: "กำลัง…", en: "Working…"},
      "common.retry": {th: "ลองใหม่", en: "Retry"},
      "common.load_failed": {th: "โหลดไม่สำเร็จ", en: "Failed to load"},
      "common.copy": {th: "คัดลอกลิงก์", en: "Copy link"},
      "common.copied": {th: "คัดลอกแล้ว", en: "Copied"},
      "errors.auth_required": {th: "โปรดเข้าสู่ระบบอีกครั้ง", en: "Please sign in again."},
      "errors.forbidden": {th: "คุณไม่มีสิทธิ์ทำรายการนี้", en: "You do not have permission for this."},
      "errors.no_account": {th: "เลือกบัญชี LINE ก่อน", en: "Select a LINE account first."},
      "errors.no_session": {th: "บัญชีนี้ยังไม่ได้เข้าสู่ระบบ LINE", en: "This account is not signed in to LINE yet."},
      "errors.not_found": {th: "ไม่พบข้อมูลที่ต้องการ", en: "Not found."},
      "errors.conflict": {th: "รายการนี้กำลังทำงานอยู่แล้ว", en: "That is already in progress."},
      "errors.login_running": {th: "กำลังเพิ่มบัญชีอยู่แล้ว", en: "A QR login is already running."},
      "errors.bad_request": {th: "ข้อมูลไม่ถูกต้อง โปรดตรวจสอบอีกครั้ง", en: "Please check your input."},
      "errors.upstream_error": {th: "บริการ LINE ขัดข้องชั่วคราว ลองใหม่อีกครั้ง", en: "LINE service error. Please try again."},
      "errors.ai_quota": {th: "โควต้า Google Gemini หมด — รุ่นฟรีมักสร้างรูปไม่ได้ (โควต้า = 0) ต้องเปิดการเรียกเก็บเงิน (billing) บนคีย์ Google หรือรอสักครู่แล้วลองใหม่", en: "Google Gemini quota exceeded — the free tier often can't generate images (quota 0). Enable billing on your Google API key, or try again later."},
      "errors.fal_quota": {th: "เครดิตหรือโควต้า fal.ai ไม่เพียงพอ โปรดตรวจสอบ Billing และยอดคงเหลือของบัญชี fal.ai", en: "fal.ai credit or quota is insufficient. Check billing and the available balance on your fal.ai account."},
      "errors.ai_key_rejected": {th: "API Key ถูกปฏิเสธหรือไม่มีสิทธิ์ใช้งาน — ตรวจสอบคีย์และสิทธิ์การใช้งานของผู้ให้บริการ", en: "The API key was rejected or lacks access — check the key and your provider access."},
      "errors.email_invalid": {th: "กรอกอีเมลให้ถูกต้อง", en: "Enter a valid email address."},
      "errors.email_exists": {th: "อีเมลนี้มีบัญชีอยู่แล้ว", en: "An account with this email already exists."},
      "errors.account_already_owned": {th: "บัญชี LINE นี้มีเจ้าของแล้วและไม่สามารถแชร์ข้ามสมาชิกได้", en: "This LINE account already belongs to another user and cannot be shared."},
      "errors.account_self_service_only": {th: "สมาชิกต้องเพิ่มและจัดการบัญชี LINE ของตนเอง", en: "Each user must add and manage their own LINE accounts."},
      "errors.password_too_short": {th: "รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร", en: "Password must be at least 8 characters."},
      "errors.password_too_long": {th: "รหัสผ่านต้องไม่เกิน 128 ตัวอักษร", en: "Password must not exceed 128 characters."},
      "errors.server_error": {th: "เกิดข้อผิดพลาดในระบบ ลองใหม่อีกครั้ง", en: "Something went wrong. Please try again."},
      "errors.line_login_required": {th: "ต้องเข้าสู่ระบบ LINE ใหม่", en: "LINE login is required again."},
      "errors.network": {th: "ติดต่อเซิร์ฟเวอร์ไม่ได้", en: "Cannot reach the server."},
      "toast.using": {th: "กำลังใช้ {name}", en: "Using {name}"},
      "toast.account_added": {th: "เพิ่มบัญชี LINE แล้ว", en: "LINE account added"},
      "toast.sent": {th: "ส่งข้อความแล้ว", en: "Message sent"},
      "toast.sending_media": {th: "กำลังส่งไฟล์…", en: "Sending…"},
      "toast.password_updated": {th: "อัปเดตรหัสผ่านแล้ว", en: "Password updated"},
      "toast.secured": {th: "ตั้งรหัสผ่านเรียบร้อย ครั้งต่อไปต้องเข้าสู่ระบบ", en: "Password set. Sign-in is now required."},
      "toast.user_created": {th: "สร้างผู้ใช้แล้ว", en: "User created"},
      "toast.user_updated": {th: "อัปเดตผู้ใช้แล้ว", en: "User updated"},
      "toast.user_deleted": {th: "ลบผู้ใช้แล้ว", en: "User deleted"},
      "toast.account_updated": {th: "อัปเดตบัญชีแล้ว", en: "Account updated"},
      "toast.account_deleted": {th: "ลบบัญชีแล้ว", en: "Account deleted"},
      "toast.schedule_created": {th: "สร้างตารางเวลาแล้ว", en: "Schedule created"},
      "toast.schedule_sent": {th: "ส่งตามตารางแล้ว", en: "Schedule sent"},
      "toast.stuck_jobs_cleared": {th: "เคลียร์งานค้างแล้ว {n} งาน", en: "Cleared {n} stuck job(s)"},
      "toast.no_stuck_jobs": {th: "ไม่มีงานค้างให้เคลียร์", en: "No stuck jobs to clear"},
      "toast.select_target_first": {th: "เลือกแชทหรือกรอกปลายทางก่อน", en: "Select a chat or enter a target first"},
      "toast.target_message_required": {th: "ต้องมีปลายทางและข้อความ", en: "Target and message are required"},
      "toast.no_session_hint": {th: "บัญชีนี้ไม่มีเซสชันที่ใช้ได้ ลองเพิ่มใหม่หรือลบทิ้ง", en: "This account has no usable session. Add it again or delete it."},
      "toast.args_json": {th: "อาร์กิวเมนต์ Endpoint ต้องเป็น JSON", en: "Endpoint args must be JSON"},
      "toast.image_required": {th: "ต้องระบุแหล่งรูปภาพ", en: "Image source is required"},
      "toast.api_url_required": {th: "ต้องระบุ URL ของ API", en: "API URL is required"},
      "toast.message_required": {th: "ต้องมีข้อความ", en: "Message is required"},
      "toast.target_required": {th: "ต้องระบุปลายทาง", en: "Target is required"},
      "toast.pick_time": {th: "เลือกเวลาที่จะส่ง", en: "Pick a send time"},
      "toast.ai_prompt_required": {th: "ต้องระบุคำสั่งสร้างรูป", en: "An image prompt is required"},
      "toast.ai_saved": {th: "บันทึกการตั้งค่า AI แล้ว", en: "AI settings saved"},
      "toast.ai_provider_active": {th: "เปลี่ยนผู้ให้บริการ AI แล้ว", en: "AI provider changed"},
      "toast.ai_cleared": {th: "ลบคีย์แล้ว", en: "API key removed"},
      "confirm.ai_clear": {th: "ลบ API Key นี้?", en: "Remove this API key?"},
      "confirm.run_now": {th: "ส่งข้อความตอนนี้เลยหรือไม่?", en: "Send this message right now?"},
      "confirm.delete_schedule": {th: "ลบตารางเวลานี้?", en: "Delete this schedule?"},
      "confirm.delete_user": {th: "ลบผู้ใช้ {name}?", en: "Delete user {name}?"},
      "confirm.clear_bot_logs": {th: "ล้าง log บอทของบัญชีนี้?", en: "Clear bot logs for this account?"},
      "botlog.title": {th: "บันทึกบอท", en: "Bot Log"},
      "botlog.entries": {th: "{n} รายการ", en: "{n} entries"},
      "botlog.refresh": {th: "รีเฟรช", en: "Refresh"},
      "botlog.clear": {th: "ล้าง log", en: "Clear"},
      "botlog.none": {th: "ยังไม่มี log บอทสำหรับบัญชีนี้", en: "No bot log for this account yet"},
      "botlog.ok": {th: "สำเร็จ", en: "OK"},
      "botlog.fail": {th: "ผิดพลาด", en: "Failed"},
      "botlog.action.schedule.create": {th: "สร้างตารางเวลา", en: "Schedule created"},
      "botlog.action.schedule.update": {th: "แก้ไขตารางเวลา", en: "Schedule updated"},
      "botlog.action.schedule.pause": {th: "หยุดตารางเวลา", en: "Schedule paused"},
      "botlog.action.schedule.resume": {th: "เปิดตารางเวลา", en: "Schedule resumed"},
      "botlog.action.schedule.delete": {th: "ลบตารางเวลา", en: "Schedule deleted"},
      "botlog.action.schedule.clear_stuck": {th: "เคลียร์งานค้าง", en: "Stuck job cleared"},
      "botlog.action.schedule.run.start": {th: "เริ่มรันบอท", en: "Bot run started"},
      "botlog.action.schedule.run.success": {th: "ส่งสำเร็จ", en: "Send succeeded"},
      "botlog.action.schedule.run.error": {th: "ส่งผิดพลาด", en: "Send failed"},
      "botlog.action.content.text": {th: "เตรียมข้อความ", en: "Text prepared"},
      "botlog.action.content.image.uploaded": {th: "เตรียมรูปที่อัปโหลด", en: "Uploaded image prepared"},
      "botlog.action.content.image.fetch": {th: "ดึงรูปจาก URL", en: "Image fetch started"},
      "botlog.action.content.image.loaded": {th: "ดึงรูปสำเร็จ", en: "Image loaded"},
      "botlog.action.content.image.error": {th: "ดึงรูปผิดพลาด", en: "Image fetch failed"},
      "botlog.action.content.api.fetch": {th: "เรียก API", en: "API fetch started"},
      "botlog.action.content.api.loaded": {th: "อ่านข้อมูล API สำเร็จ", en: "API content loaded"},
      "botlog.action.content.api.error": {th: "เรียก API ผิดพลาด", en: "API fetch failed"},
      "botlog.action.content.ai.start": {th: "เริ่มสร้างรูป AI", en: "AI image generation started"},
      "botlog.action.content.ai.request": {th: "ส่งคำสั่งไป AI", en: "AI request sent"},
      "botlog.action.content.ai.task": {th: "AI รับงานแล้ว", en: "AI task accepted"},
      "botlog.action.content.ai.poll": {th: "ตรวจสถานะ AI", en: "AI status checked"},
      "botlog.action.content.ai.download": {th: "ดาวน์โหลดรูปจาก AI", en: "AI image downloaded"},
      "botlog.action.content.ai.success": {th: "สร้างรูป AI สำเร็จ", en: "AI image generated"},
      "botlog.action.content.ai.error": {th: "สร้างรูป AI ผิดพลาด", en: "AI image generation failed"},
      "botlog.action.send.item.success": {th: "ส่งรายการสำเร็จ", en: "Item sent"},
      "botlog.action.send.item.error": {th: "ส่งรายการผิดพลาด", en: "Item send failed"},
      "botlog.action.pattern.create": {th: "เพิ่มแพทเทิร์น", en: "Pattern created"},
      "botlog.action.pattern.delete": {th: "ลบแพทเทิร์น", en: "Pattern deleted"},
      "botlog.action.logs.clear": {th: "ล้าง log", en: "Logs cleared"},
      "toast.bot_logs_cleared": {th: "ล้าง log บอทแล้ว", en: "Bot logs cleared"}
    };

    const state = {
      profile: null,
      target: "",
      loginTimer: null,
      loginStep: 1,
      accounts: [],
      activeAccountId: null,
      schedules: [],
      scheduleLoading: false,
      botLogs: [],
      botLogLoading: false,
      webAuthConfigured: false,
      authMode: "login",
      simpleMode: false,
      currentUser: null,
      roles: {},
      managementAccounts: [],
      users: [],
      patterns: [],
      patternCategories: [],
      patternCategoryFilter: "all",
      lang: "th",
      advanced: true,
      tab: "line",
      botPage: "schedules",
      contactsSubtab: "people",
      e2eeReady: false,
      sending: false,
      lastStatus: null,
      authRetryTimer: null
    };

    const BOT_BACKGROUND_REFRESH_MS = 3000;

    const $ = (id) => document.getElementById(id);

    Object.assign(I18N, {
      "password.confirm": {th: "ยืนยันรหัสผ่านใหม่", en: "Confirm new password"},
      "patterns.category": {th: "หมวดหมู่", en: "Category"},
      "patterns.categories_title": {th: "หมวดหมู่", en: "Categories"},
      "patterns.manage_title": {th: "จัดการหมวดหมู่", en: "Manage categories"},
      "patterns.manage_hint": {th: "เพิ่ม แก้ไข หรือลบหมวดหมู่แพทเทิร์น", en: "Add, edit, or delete pattern categories"},
      "patterns.settings": {th: "ตั้งค่าหมวดหมู่", en: "Category settings"},
      "patterns.system": {th: "หมวดระบบ", en: "System category"},
      "patterns.edit_category": {th: "แก้ไขหมวดหมู่", en: "Edit category"},
      "patterns.category_updated": {th: "แก้ไขหมวดหมู่แล้ว", en: "Category updated"},
      "patterns.create_title": {th: "สร้างแพทเทิร์นใหม่", en: "Create pattern"},
      "patterns.list_title": {th: "รายการแพทเทิร์น", en: "Patterns"},
      "patterns.category_filter": {th: "กรองตามหมวดหมู่", en: "Filter by category"},
      "patterns.general": {th: "ทั่วไป", en: "General"},
      "patterns.all": {th: "ทุกหมวดหมู่", en: "All categories"},
      "patterns.add_category": {th: "+ เพิ่มหมวดหมู่", en: "+ Add category"},
      "patterns.delete_category": {th: "ลบหมวดหมู่", en: "Delete category"},
      "patterns.category_name": {th: "ชื่อหมวดหมู่", en: "Category name"},
      "patterns.category_name_ph": {th: "เช่น โปรโมชั่น", en: "e.g. Promotions"},
      "patterns.category_created": {th: "เพิ่มหมวดหมู่แล้ว", en: "Category created"},
      "patterns.category_deleted": {th: "ลบหมวดหมู่แล้ว", en: "Category deleted"},
      "patterns.delete_category_confirm": {th: "ลบหมวดหมู่นี้? แพทเทิร์นในหมวดจะย้ายไปทั่วไป", en: "Delete this category? Its patterns will move to General."},
      "botlog.action.pattern.category.create": {th: "เพิ่มหมวดหมู่แพทเทิร์น", en: "Pattern category created"},
      "botlog.action.pattern.category.delete": {th: "ลบหมวดหมู่แพทเทิร์น", en: "Pattern category deleted"},
      "botlog.action.pattern.category.update": {th: "แก้ไขหมวดหมู่แพทเทิร์น", en: "Pattern category updated"},
      "gate.none_shared_title": {th: "ยังไม่มีบัญชี LINE", en: "No LINE account yet"},
      "gate.none_shared_body": {th: "เพิ่มบัญชี LINE ของคุณได้จากเมนูตั้งค่า", en: "Add your own LINE account from Settings."},
      "settings.subtitle_users": {th: "จัดการสมาชิก บทบาท และตรวจดูพื้นที่ใช้งานของแต่ละคน", en: "Manage members, roles, and inspect each private workspace."},
      "users.subtitle": {th: "สมาชิกแต่ละคนมีพื้นที่ส่วนตัวและเพิ่มบัญชี LINE ของตนเอง", en: "Each member has a private workspace and adds their own LINE accounts."}
    });

    function t(key, vars) {
      const entry = I18N[key];
      let s = entry ? (entry[state.lang] != null ? entry[state.lang] : entry.en) : key;
      if (vars) for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
      return s;
    }

    function textSpan(text, cls) {
      const s = document.createElement("span");
      if (cls) s.className = cls;
      s.textContent = text;
      return s;
    }

    function applyI18n() {
      document.documentElement.lang = state.lang;
      document.querySelectorAll("[data-i18n]").forEach((el) => {
        el.textContent = t(el.dataset.i18n);
      });
      document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
        el.setAttribute("placeholder", t(el.dataset.i18nPh));
      });
      document.querySelectorAll("[data-i18n-title]").forEach((el) => {
        el.setAttribute("title", t(el.dataset.i18nTitle));
      });
      $("openPatternCategoriesButton").setAttribute("aria-label", t("patterns.settings"));
      updateMenuToggleLabels();
      renderSchedules();
      renderBotLogs();
      updateScheduleSummary();
      renderAuthMode();
      if (state.aiSettings) renderAiSettings(state.aiSettings);
      if (state.lastStatus) renderProfile(state.lastStatus);
      applyPermissions();
    }

    function setLang(lang) {
      state.lang = lang === "en" ? "en" : "th";
      localStorage.setItem("okline.lang", state.lang);
      applyI18n();
    }

    function applyAdvanced() {
      state.advanced = true;
      document.body.classList.add("advanced");
      updateMenuToggleLabels();
      applyPermissions();
    }

    // The language toggle lives inside the Settings menu and reflects its
    // current target language.
    function updateMenuToggleLabels() {
      $("langToggle").textContent = t("menu.language") + ": " + (state.lang === "th" ? "EN" : "ไทย");
    }

    // ---- top-level tabs (LINE / Tools / Bot / AI) ----------------------
    const TABS = ["line", "tools", "bot", "ai"];
    const BOT_PAGES = ["schedules", "schedule", "patterns", "pattern-categories", "logs"];
    function setTab(tab) {
      if (!TABS.includes(tab)) tab = "line";
      state.tab = tab;
      localStorage.setItem("okline.tab", tab);
      document.querySelectorAll(".tabbar .tab").forEach((btn) => {
        const on = btn.dataset.tab === tab;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-selected", String(on));
      });
      document.querySelectorAll("[data-tab-panel]").forEach((p) => {
        p.classList.toggle("active", p.dataset.tabPanel === tab);
      });
      if (tab === "bot") {
        setBotPage(state.botPage || "schedules");
        loadPatterns();
        loadSchedules().catch(toastError);
        loadBotLogs().catch(toastError);
      }
      if (tab === "ai") loadAiSettings();
    }

    function setBotPage(page) {
      if (!BOT_PAGES.includes(page)) page = "schedules";
      state.botPage = page;
      document.querySelectorAll("[data-bot-page]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.dataset.botPage !== page);
      });
      document.querySelectorAll("[data-bot-page-target]").forEach((btn) => {
        const on = btn.dataset.botPageTarget === page
          || (page === "pattern-categories" && btn.dataset.botPageTarget === "patterns");
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      });
      if (page === "patterns") loadPatterns();
      if (page === "pattern-categories") loadPatterns();
      if (page === "schedules") loadSchedules().catch(toastError);
      if (page === "logs") loadBotLogs().catch(toastError);
    }

    // ---- contacts sub-tabs (people / groups) ---------------------------
    function setContactsSubtab(which) {
      const groups = which === "groups";
      state.contactsSubtab = groups ? "groups" : "people";
      $("loadContactsButton").setAttribute("aria-selected", String(!groups));
      $("loadGroupsButton").setAttribute("aria-selected", String(groups));
      $("contactsList").classList.toggle("hidden", groups);
      $("groupsList").classList.toggle("hidden", !groups);
      $("contactSearchRow").classList.toggle("hidden", groups);
    }

    function toast(message, isError = false) {
      const el = $("toast");
      $("toastText").textContent = message;
      el.style.borderColor = isError ? "#f1b9b5" : "";
      el.style.color = isError ? "#b42318" : "";
      $("toastClose").hidden = !isError;
      el.classList.add("show");
      clearTimeout(el._timer);
      const duration = isError ? 8000 : 3200;
      el._timer = setTimeout(hideToast, duration);
    }

    function hideToast() {
      const el = $("toast");
      clearTimeout(el._timer);
      el.classList.remove("show");
    }

    function toastError(err) {
      const code = err && err.code;
      if (code === "no_account" && state.accounts.length === 0) return;
      let msg;
      if (code && I18N["errors." + code]) msg = t("errors." + code);
      else msg = (err && err.message) || t("errors.server_error");
      if (err) console.error("LinePassport error:", code || "", err.raw || err.message || err);
      toast(msg, true);
    }

    async function api(path, options = {}) {
      const init = {
        credentials: "same-origin",
        ...options,
        headers: {"content-type": "application/json", ...(options.headers || {})}
      };
      let res;
      try {
        res = await fetch(path, init);
      } catch (netErr) {
        const e = new Error(t("errors.network"));
        e.code = "network";
        e.raw = netErr;
        throw e;
      }
      const text = await res.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {raw: text}; }
      if (!res.ok) {
        if (res.status === 401 && data.code === "auth_required") showAuthPanel(true, true);
        if (res.status === 403) applyPermissions();
        const e = new Error(data.error || data.message || res.statusText);
        e.code = data.code || "";
        e.raw = data.detail || data.error;
        throw e;
      }
      return data;
    }

    const post = (path, body = {}) => api(path, {method: "POST", body: JSON.stringify(body)});

    function setPill(id, text, kind) {
      const el = $(id);
      el.textContent = text;
      el.className = "pill" + (kind ? " " + kind : "");
    }

    function authMode() {
      if (!state.webAuthConfigured) return "setup";
      return state.authMode === "register" ? "register" : "login";
    }

    function renderAuthMode() {
      const mode = authMode();
      const isSetup = mode === "setup";
      const isRegister = mode === "register";
      $("authModeLabel").textContent = isSetup
        ? t("auth.create_label")
        : isRegister ? t("auth.register_label") : t("auth.signin_label");
      $("webAuthButton").textContent = isSetup
        ? t("auth.create_btn")
        : isRegister ? t("auth.register") : t("auth.signin");
      $("webAuthHint").textContent = isSetup
        ? t("auth.hint_create")
        : isRegister ? t("auth.hint_register") : t("auth.hint_signin");
      $("confirmPasswordRow").classList.toggle("hidden", !(isSetup || isRegister));
      $("registrationNameRow").classList.toggle("hidden", !isRegister);
      $("authIdentityLabel").textContent = t("auth.email");
      $("webUsernameInput").type = "email";
      $("webUsernameInput").placeholder = "name@example.com";
      $("authSwitchRow").classList.toggle("hidden", isSetup);
      $("authSwitchText").textContent = isRegister ? t("auth.have_account") : t("auth.need_account");
      $("authModeToggle").textContent = isRegister ? t("auth.back_to_signin") : t("auth.register");
      $("webPasswordInput").autocomplete = isSetup || isRegister ? "new-password" : "current-password";
    }

    function setAuthMode(mode) {
      state.authMode = mode === "register" ? "register" : "login";
      $("webUsernameInput").value = "";
      $("registrationNameInput").value = "";
      $("webPasswordInput").value = "";
      $("confirmPasswordInput").value = "";
      renderAuthMode();
      $("webUsernameInput").focus();
    }

    function showAuthPanel(show, configured = state.webAuthConfigured) {
      const panel = $("authPanel");
      const wasHidden = panel.classList.contains("hidden");
      const userIsEditing = panel.contains(document.activeElement);
      state.webAuthConfigured = configured;
      if (!configured) state.authMode = "setup";
      else if (state.authMode === "setup") state.authMode = "login";
      panel.classList.toggle("hidden", !show);
      $("appShell").classList.toggle("app-hidden", show);
      renderAuthMode();
      $("webAuthButton").disabled = false;
      if (show && wasHidden && !userIsEditing) {
        $("webUsernameInput").focus({preventScroll: true});
      }
    }

    function setView(view) {
      document.body.classList.toggle("login-page-scroll", view === "login");
      $("accountGate").classList.toggle("hidden", view !== "gate");
      $("loginPanel").classList.toggle("active", view === "login");
      $("settingsPanel").classList.toggle("active", view === "settings");
      $("workspace").classList.toggle("hidden", view !== "workspace");
    }

    function showGate(noAccess) {
      $("accountGateDefault").classList.toggle("hidden", !!noAccess);
      $("accountGateNoAccess").classList.toggle("hidden", !noAccess);
      setView("gate");
    }

    async function goHome() {
      closeSettingsMenu();
      if ($("loginPanel").classList.contains("active")) {
        clearInterval(state.loginTimer);
        $("beginAddAccountButton").disabled = false;
        try { await post("/api/login/cancel"); } catch (_) { /* best effort */ }
      }
      setTab("line");
      const accountId = state.activeAccountId || selectedAccountId();
      if (accountId) {
        state.activeAccountId = accountId;
        renderAccounts();
        setView("workspace");
        setAccountControls(Boolean(state.lastStatus && state.lastStatus.authenticated));
        return;
      }
      await refreshStatus(false);
    }

    function setAccountControls(enabled) {
      document.querySelectorAll("[data-requires-account]").forEach((el) => {
        el.disabled = !enabled;
      });
      applyPermissions();
      if (enabled) populatePatternCategoryControls();
      updateE2eeButton();
    }

    function hasPermission(permission) {
      const perms = state.currentUser && state.currentUser.permissions ? state.currentUser.permissions : [];
      return perms.includes(permission);
    }

    function applyPermissions() {
      const canManageAccounts = hasPermission("manage_accounts");
      const canManageUsers = hasPermission("manage_users");
      const showUsers = canManageUsers && (!state.simpleMode || state.advanced);
      $("addAccountButton").disabled = !canManageAccounts;
      $("addAccountButton").classList.toggle("hidden", !canManageAccounts);
      $("accountsTabButton").classList.toggle("hidden", !canManageAccounts);
      $("usersTabButton").classList.toggle("hidden", !showUsers);
      $("userManagementHead").classList.toggle("hidden", !showUsers);
      $("createUserCard").classList.toggle("hidden", !showUsers);
      $("userList").classList.toggle("hidden", !showUsers);
      $("secureCard").classList.toggle("hidden", !(state.simpleMode && canManageUsers && state.advanced));
      $("accountManagementMenuButton").disabled = !canManageAccounts && !canManageUsers;
      $("accountManagementMenuButton").classList.toggle("hidden", !canManageAccounts);
      $("userManagementMenuButton").disabled = !showUsers;
      $("userManagementMenuButton").classList.toggle("hidden", !showUsers);
      $("changePasswordMenuButton").classList.toggle("hidden", state.simpleMode);
      $("passwordTabButton").classList.toggle("hidden", state.simpleMode);
      $("webLogoutButton").classList.toggle("hidden", state.simpleMode);
      document.querySelectorAll("[data-requires-permission]").forEach((el) => {
        if (!hasPermission(el.dataset.requiresPermission)) {
          el.disabled = true;
          el.title = t("perm.denied");
        } else {
          if (el.title === t("perm.denied")) el.removeAttribute("title");
          // Re-enable permission-only controls (e.g. AI Settings). Controls that
          // also gate on an account are owned by setAccountControls(); leave them.
          if (!el.hasAttribute("data-requires-account")) el.disabled = false;
        }
      });
    }

    function updateE2eeButton() {
      const btn = $("sendEncryptedButton");
      if (!btn) return;
      if (!state.e2eeReady) {
        btn.disabled = true;
        btn.title = t("chat.e2ee_disabled_tip");
      } else if (btn.title === t("chat.e2ee_disabled_tip")) {
        btn.removeAttribute("title");
      }
    }

    function selectedAccountId() {
      return ($("accountSelect").value || "").trim();
    }

    function setAccountSwitchLoading(loading) {
      const on = Boolean(loading);
      $("accountSwitchLoading").classList.toggle("hidden", !on);
      $("accountSwitchLoading").setAttribute("aria-hidden", String(!on));
      $("appShell").setAttribute("aria-busy", String(on));
      $("accountSelect").disabled = on;
    }

    function requireAccount() {
      const accountId = selectedAccountId();
      if (!accountId) {
        setAccountControls(false);
        // A clean member workspace has no account to select yet. Background
        // loaders should stay quiet and leave the Add Account wizard visible.
        if (!state.accounts.length) return "";
        showGate(false);
        toast(t("errors.no_account"), true);
        return "";
      }
      return accountId;
    }

    function accountQuery(accountId) {
      return `accountId=${encodeURIComponent(accountId)}`;
    }

    function clearLists() {
      $("contactsList").replaceChildren();
      $("groupsList").replaceChildren();
      $("messagesList").replaceChildren();
      state.schedules = [];
      renderSchedules();
    }

    function clearProfile() {
      state.profile = null;
      state.e2eeReady = false;
      $("profileName").textContent = t("profile.no_account");
      $("profileMid").textContent = "-";
      $("profileUserId").textContent = "-";
      $("profileStatus").textContent = "-";
      $("profileE2ee").textContent = "-";
      setPill("authPill", t("pills.select_account"), "warn");
      setPill("nodePill", t("pills.node"), "");
      $("chatTitle").textContent = t("chat.title");
      $("targetLabel").textContent = t("chat.no_target");
    }

    function renderProfile(status) {
      state.lastStatus = status;
      setPill("nodePill", status.nodeAvailable ? t("pills.node_ready") : t("pills.node_missing"), status.nodeAvailable ? "ok" : "warn");
      setPill("authPill", status.authenticated ? t("pills.connected") : t("pills.not_connected"), status.authenticated ? "ok" : "warn");

      state.accounts = status.accounts || [];
      state.activeAccountId = status.activeAccountId || state.activeAccountId || null;
      state.e2eeReady = !!status.e2eeReady;
      renderAccounts();

      const p = status.profile || {};
      state.profile = p;
      $("profileName").textContent = p.displayName || accountLabel(state.activeAccountId) || t("profile.selected");
      $("profileMid").textContent = p.mid || "-";
      $("profileUserId").textContent = p.userid || "-";
      $("profileStatus").textContent = p.statusMessage || "-";
      $("profileE2ee").textContent = status.e2eeReady ? t("profile.e2ee_ready") : t("profile.e2ee_off");
      updateE2eeButton();
    }

    function accountLabel(accountId) {
      const account = state.accounts.find((a) => a.id === accountId);
      return account ? (account.label || account.id) : "";
    }

    function renderAccounts() {
      const selected = state.activeAccountId || "";
      for (const select of [$("accountSelect"), $("scheduleAccount")]) {
        select.replaceChildren();
        if (select.id === "accountSelect") {
          const placeholder = document.createElement("option");
          placeholder.value = "";
          placeholder.textContent = state.accounts.length ? t("accounts.select") : t("accounts.none_yet");
          select.appendChild(placeholder);
        }
        for (const account of state.accounts) {
          const opt = document.createElement("option");
          opt.value = account.id;
          opt.textContent = `${account.label || account.id}${account.tokenFileExists ? "" : " (" + t("accounts.missing") + ")"}`;
          select.appendChild(opt);
        }
        select.value = selected;
      }
      renderLineAccountList(state.managementAccounts.length ? state.managementAccounts : state.accounts);
      applyPermissions();
    }

    function closeSettingsMenu() {
      $("settingsMenu").classList.add("hidden");
      $("settingsButton").setAttribute("aria-expanded", "false");
    }

    function toggleSettingsMenu() {
      const menu = $("settingsMenu");
      const isHidden = menu.classList.toggle("hidden");
      $("settingsButton").setAttribute("aria-expanded", String(!isHidden));
    }

    function openSettings(pane) {
      pane = pane || (state.simpleMode ? "accounts" : "password");
      closeSettingsMenu();
      setSettingsPane(pane);
      setAccountControls(false);
      setView("settings");
    }

    function setSettingsPane(pane) {
      if (!["password", "accounts", "users"].includes(pane)) pane = "accounts";
      $("passwordPane").classList.toggle("active", pane === "password");
      $("accountsPane").classList.toggle("active", pane === "accounts");
      $("usersPane").classList.toggle("active", pane === "users");
      $("passwordTabButton").classList.toggle("active", pane === "password");
      $("accountsTabButton").classList.toggle("active", pane === "accounts");
      $("usersTabButton").classList.toggle("active", pane === "users");
      const subtitleKey = pane === "password"
        ? "settings.subtitle_password"
        : (pane === "users" ? "settings.subtitle_users" : "settings.subtitle_line_accounts");
      $("settingsSubtitle").textContent = t(subtitleKey);
      if (pane === "accounts" || pane === "users") loadAccountManagement().catch(toastError);
    }

    async function changePassword() {
      const currentPassword = $("currentPasswordInput").value;
      const newPassword = $("newPasswordInput").value;
      const confirmNewPassword = $("confirmNewPasswordInput").value;
      if (newPassword !== confirmNewPassword) {
        toast(t("auth.password_mismatch"), true);
        $("confirmNewPasswordInput").focus();
        return;
      }
      await post("/api/auth/change-password", {currentPassword, newPassword});
      $("currentPasswordInput").value = "";
      $("newPasswordInput").value = "";
      $("confirmNewPasswordInput").value = "";
      toast(t("toast.password_updated"));
    }

    async function secureWithPassword() {
      const res = await openModal({
        title: t("secure.title"),
        build: (body) => {
          body.appendChild(textSpan(t("secure.body"), "muted"));
          const u = labeledInput(t("auth.email"), "email");
          u.input.placeholder = "admin@example.com";
          const p = labeledInput(t("auth.password"), "password");
          const c = labeledInput(t("auth.confirm"), "password");
          body.append(u.label, p.label, c.label);
          return () => ({email: u.input.value.trim(), password: p.input.value, confirm: c.input.value});
        },
        confirmKey: "secure.action"
      });
      if (!res) return;
      if (res.password !== res.confirm) { toast(t("auth.password_mismatch"), true); return; }
      await post("/api/auth/secure", {email: res.email, password: res.password});
      toast(t("toast.secured"));
      state.simpleMode = false;
      document.body.classList.remove("simple-mode");
      applyPermissions();
      await refreshWebAuth();
    }

    async function loadAccountManagement() {
      if (hasPermission("manage_users")) {
        const data = await api("/api/users");
        state.users = data.users || [];
        state.roles = data.roles || state.roles || {};
        state.managementAccounts = data.accounts || [];
      } else {
        const data = await api("/api/accounts");
        state.managementAccounts = data.accounts || [];
        state.users = [];
      }
      renderLineAccountList(state.managementAccounts);
      renderRoleOptions();
      renderUsers();
      applyPermissions();
    }

    function renderLineAccountList(accounts) {
      const list = $("lineAccountList");
      if (!list) return;
      list.replaceChildren();
      for (const account of accounts || []) {
        const row = document.createElement("div");
        row.className = "management-row";
        const info = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = account.label || account.id;
        const meta = document.createElement("span");
        meta.className = "mono";
        meta.textContent = account.mid || account.id;
        const sub = document.createElement("span");
        sub.className = "muted";
        sub.textContent = account.tokenFileExists ? t("accounts.session_ready") : t("accounts.session_missing");
        info.append(title, meta, sub);

        const actions = document.createElement("div");
        actions.className = "management-row-actions";
        const use = document.createElement("button");
        use.textContent = t("accounts.use");
        use.addEventListener("click", async () => {
          state.activeAccountId = account.id;
          renderAccounts();
          await selectAccount().catch(toastError);
        });
        const edit = document.createElement("button");
        edit.textContent = t("common.edit");
        edit.disabled = !hasPermission("manage_accounts");
        edit.addEventListener("click", () => editLineAccount(account).catch(toastError));
        const del = document.createElement("button");
        del.className = "danger";
        del.textContent = t("common.delete");
        del.disabled = !hasPermission("manage_accounts");
        del.addEventListener("click", () => deleteLineAccount(account).catch(toastError));
        actions.append(use, edit, del);
        row.append(info, actions);
        list.appendChild(row);
      }
      if (!list.children.length) list.appendChild(textSpan(t("accounts.none"), "muted"));
    }

    async function editLineAccount(account) {
      const res = await openModal({
        title: t("accounts.edit_title"),
        build: (body) => {
          const li = labeledInput(t("accounts.name"), "text");
          li.input.value = account.label || account.id;
          body.appendChild(li.label);
          return () => li.input.value.trim();
        },
        confirmKey: "common.save"
      });
      if (res == null || res === "") return;
      await post("/api/accounts/update", {accountId: account.id, label: res});
      toast(t("toast.account_updated"));
      await refreshStatus(false);
      await loadAccountManagement();
    }

    async function deleteLineAccount(account) {
      const res = await openModal({
        title: t("accounts.delete_title", {name: account.label || account.id}),
        build: (body) => {
          body.appendChild(textSpan(t("accounts.delete_note"), "muted"));
          const label = document.createElement("label");
          label.className = "checkbox-row";
          const cb = document.createElement("input");
          cb.type = "checkbox";
          label.append(cb, textSpan(t("accounts.also_line_logout")));
          body.appendChild(label);
          return () => ({ok: true, logout: cb.checked});
        },
        confirmKey: "accounts.delete",
        danger: true
      });
      if (!res) return;
      if (res.logout) await post("/api/logout", {accountId: account.id});
      else await post("/api/accounts/delete", {accountId: account.id});
      if (state.activeAccountId === account.id) state.activeAccountId = null;
      toast(t("toast.account_deleted"));
      await refreshStatus(false);
      await loadAccountManagement();
    }

    function renderRoleOptions() {
      const select = $("newUserRole");
      select.replaceChildren();
      for (const [role, meta] of Object.entries(state.roles || {})) {
        if (role === "god" && (!state.currentUser || state.currentUser.role !== "god")) continue;
        const opt = document.createElement("option");
        opt.value = role;
        opt.textContent = meta.label || role;
        select.appendChild(opt);
      }
      if (state.roles && state.roles.member) select.value = "member";
      else if (state.roles && state.roles.viewer) select.value = "viewer";
    }

    async function createUser() {
      const payload = {
        email: $("newUserEmail").value.trim(),
        displayName: $("newUserDisplayName").value.trim(),
        password: $("newUserPassword").value,
        role: $("newUserRole").value
      };
      const data = await post("/api/users/create", payload);
      state.users = data.users || state.users;
      state.simpleMode = false;
      document.body.classList.remove("simple-mode");
      $("newUserEmail").value = "";
      $("newUserDisplayName").value = "";
      $("newUserPassword").value = "";
      renderUsers();
      applyPermissions();
      toast(t("toast.user_created"));
    }

    function renderUsers() {
      const list = $("userList");
      if (!list) return;
      list.replaceChildren();
      for (const user of state.users || []) {
        const row = document.createElement("div");
        row.className = "management-row";
        const info = document.createElement("div");
        const title = document.createElement("strong");
        const loginName = user.email || user.username;
        title.textContent = `${user.displayName || loginName} (${loginName})`;
        const meta = document.createElement("span");
        meta.className = "muted";
        const accounts = user.role === "god"
          ? t("users.all_accounts")
          : ((user.accountIds || []).map(accountLabelFromManagement).join(", ") || t("users.no_accounts"));
        const roleLabel = (state.roles[user.role] && state.roles[user.role].label) || user.role;
        meta.textContent = `${roleLabel}${user.active ? "" : " · " + t("users.disabled")} · ${accounts}`;
        info.append(title, meta);

        const actions = document.createElement("div");
        actions.className = "management-row-actions";
        const protectedGod = user.role === "god"
          && (!state.currentUser || state.currentUser.role !== "god");
        if (protectedGod) {
          actions.appendChild(textSpan(t("users.god_protected"), "muted"));
          row.append(info, actions);
          list.appendChild(row);
          continue;
        }
        if (state.currentUser && state.currentUser.role === "god") {
          const details = document.createElement("button");
          details.textContent = t("users.details");
          details.addEventListener("click", () => toggleUserDetails(user, row).catch(toastError));
          actions.appendChild(details);
        }
        const edit = document.createElement("button");
        edit.textContent = t("common.edit");
        edit.addEventListener("click", () => toggleUserEdit(user, row));
        const active = document.createElement("button");
        active.textContent = user.active ? t("users.disable") : t("users.enable");
        active.addEventListener("click", () => updateUser(user.id, {active: !user.active}).catch(toastError));
        const del = document.createElement("button");
        del.className = "danger";
        del.textContent = t("common.delete");
        del.addEventListener("click", () => deleteUser(user).catch(toastError));
        actions.append(edit, active, del);
        row.append(info, actions);
        list.appendChild(row);
      }
      if (!list.children.length) list.appendChild(textSpan(t("users.none"), "muted"));
    }

    async function toggleUserDetails(user, row) {
      const existing = row.querySelector(".user-detail-panel");
      if (existing) { existing.remove(); return; }
      const data = await api(`/api/users/detail?userId=${encodeURIComponent(user.id)}`);
      const panel = document.createElement("div");
      panel.className = "inline-edit user-detail-panel";

      const addSection = (title, values) => {
        const section = document.createElement("div");
        section.className = "stack compact";
        section.appendChild(textSpan(title, "strong"));
        if (!values.length) section.appendChild(textSpan(t("users.detail_empty"), "muted"));
        else values.forEach((value) => section.appendChild(textSpan(value, "muted")));
        panel.appendChild(section);
      };

      addSection(t("users.detail_line"), (data.accounts || []).map((account) =>
        `${account.label || account.id} · ${account.tokenFileExists ? t("accounts.session_ready") : t("accounts.session_missing")}`
      ));
      addSection(t("users.detail_patterns"), (data.patterns || []).map((pattern) =>
        `${pattern.name || "-"}: ${pattern.text || ""}`
      ));
      addSection(t("users.detail_schedules"), (data.schedules || []).map((job) =>
        `${job.name || "-"} · ${job.status || "-"} · ${t("scheduler.sent")}: ${job.sentCount || 0}`
      ));
      const ai = data.ai || {};
      const aiName = ai.modelLabel || ai.model || "-";
      addSection(t("users.detail_ai"), [
        `${ai.providerLabel || ai.provider || "-"} · ${aiName} · ${ai.configured ? t("users.ai_configured") : t("users.ai_not_configured")}`
      ]);
      row.appendChild(panel);
    }

    function toggleUserEdit(user, row) {
      const existing = row.querySelector(".user-edit-panel");
      if (existing) { existing.remove(); return; }
      const panel = document.createElement("div");
      panel.className = "inline-edit user-edit-panel";

      const identityGrid = document.createElement("div");
      identityGrid.className = "two-col";
      const email = labeledInput(t("users.email"), "email");
      email.input.value = user.email || "";
      email.input.placeholder = user.email ? "" : user.username;
      email.input.autocomplete = "off";
      const displayName = labeledInput(t("users.displayname"), "text");
      displayName.input.value = user.displayName || "";
      identityGrid.append(email.label, displayName.label);
      panel.appendChild(identityGrid);

      const roleLabel = document.createElement("label");
      roleLabel.append(textSpan(t("users.role")));
      const roleSel = document.createElement("select");
      for (const [role, m] of Object.entries(state.roles || {})) {
        if (role === "god" && (!state.currentUser || state.currentUser.role !== "god")) continue;
        const o = document.createElement("option");
        o.value = role;
        o.textContent = m.label || role;
        roleSel.appendChild(o);
      }
      roleSel.value = user.role;
      roleLabel.append(roleSel);
      panel.appendChild(roleLabel);

      panel.appendChild(textSpan(t("users.admin_all_note"), "muted"));

      const pw = labeledInput(t("users.new_password_optional"), "password");
      pw.input.autocomplete = "new-password";
      panel.appendChild(pw.label);

      const save = document.createElement("button");
      save.className = "primary";
      save.textContent = t("common.save");
      save.addEventListener("click", async () => {
        const patch = {
          displayName: displayName.input.value.trim(),
          role: roleSel.value
        };
        if (email.input.value.trim()) patch.email = email.input.value.trim();
        if (pw.input.value) patch.password = pw.input.value;
        try { await updateUser(user.id, patch); } catch (err) { toastError(err); }
      });
      panel.appendChild(save);
      row.appendChild(panel);
    }

    function accountLabelFromManagement(accountId) {
      const account = (state.managementAccounts || []).find((a) => a.id === accountId);
      return account ? (account.label || account.id) : accountId;
    }

    async function updateUser(userId, patch) {
      const data = await post("/api/users/update", {id: userId, ...patch});
      state.users = data.users || state.users;
      renderUsers();
      toast(t("toast.user_updated"));
    }

    async function deleteUser(user) {
      const res = await openModal({
        title: t("confirm.delete_user", {name: user.username}),
        confirmKey: "common.delete",
        danger: true
      });
      if (!res) return;
      const data = await post("/api/users/delete", {id: user.id});
      state.users = data.users || [];
      renderUsers();
      toast(t("toast.user_deleted"));
    }

    async function submitWebAuth() {
      const email = $("webUsernameInput").value.trim();
      const password = $("webPasswordInput").value;
      const mode = authMode();
      if ((mode === "setup" || mode === "register") && !$("webUsernameInput").validity.valid) {
        toast(t("auth.email_invalid"), true);
        return;
      }
      if (mode === "setup" || mode === "register") {
        if (password !== $("confirmPasswordInput").value) {
          toast(t("auth.password_mismatch"), true);
          return;
        }
      }
      $("webAuthButton").disabled = true;
      try {
        const endpoint = mode === "setup"
          ? "/api/auth/setup"
          : mode === "register" ? "/api/auth/register" : "/api/auth/login";
        const data = await post(endpoint, {
          email,
          password,
          displayName: mode === "register" ? $("registrationNameInput").value.trim() : ""
        });
        state.webAuthConfigured = data.configured;
        state.authMode = "login";
        state.simpleMode = false;
        state.currentUser = data.user || null;
        state.roles = data.roles || {};
        document.body.classList.remove("simple-mode");
        $("webPasswordInput").value = "";
        $("confirmPasswordInput").value = "";
        $("registrationNameInput").value = "";
        hideToast();
        showAuthPanel(false);
        applyPermissions();
        await refreshStatus(false);
      } finally {
        $("webAuthButton").disabled = false;
      }
    }

    async function refreshWebAuth() {
      $("webAuthButton").disabled = true;
      $("webAuthHint").textContent = t("auth.hint_checking");
      let data;
      try {
        data = await api("/api/auth/status");
      } catch (err) {
        console.error("LinePassport status error:", err && (err.raw || err.message));
        $("authPanel").classList.remove("hidden");
        $("appShell").classList.add("app-hidden");
        $("webAuthHint").textContent = t("auth.offline");
        $("webAuthButton").disabled = true;
        clearTimeout(state.authRetryTimer);
        state.authRetryTimer = setTimeout(() => refreshWebAuth(), 3000);
        return;
      }
      clearTimeout(state.authRetryTimer);
      state.webAuthConfigured = data.configured;
      state.simpleMode = !!data.simpleMode;
      state.currentUser = data.user || null;
      state.roles = data.roles || {};
      document.body.classList.toggle("simple-mode", state.simpleMode);
      if (state.simpleMode || data.authenticated) {
        showAuthPanel(false, data.configured);
        applyPermissions();
        await refreshStatus(false);
        return;
      }
      showAuthPanel(true, data.configured);
    }

    async function webLogout() {
      await post("/api/auth/logout");
      state.activeAccountId = null;
      state.currentUser = null;
      state.roles = {};
      clearProfile();
      clearLists();
      closeSettingsMenu();
      showAuthPanel(true, true);
    }

    async function selectAccount() {
      const accountId = selectedAccountId();
      setTab("line");
      state.activeAccountId = accountId || null;
      clearLists();
      clearProfile();
      renderAccounts();
      if (!accountId) {
        setAccountSwitchLoading(false);
        showGate(false);
        setAccountControls(false);
        return;
      }
      setView("workspace");
      setAccountControls(false);
      setAccountSwitchLoading(true);
      try {
        const data = await post("/api/accounts/switch", {accountId});
        renderProfile(data.status);
        setView("workspace");
        setAccountControls(Boolean(data.status.authenticated));
        toast(t("toast.using", {name: accountLabel(accountId) || accountId}));
        if (!data.status.authenticated) {
          toast(t("toast.no_session_hint"), true);
          return;
        }
        await Promise.allSettled([loadContacts(), loadGroups(), loadSchedules(), loadBotLogs()]);
      } finally {
        setAccountSwitchLoading(false);
      }
    }

    async function refreshStatus(loadData = true) {
      try {
        const accountId = state.activeAccountId || "";
        const suffix = accountId ? `?${accountQuery(accountId)}` : "";
        const status = await api(`/api/status${suffix}`);
        if (!accountId) status.activeAccountId = "";
        renderProfile(status);
        if (!state.accounts.length) {
          setAccountControls(false);
          if (hasPermission("manage_accounts")) openAddAccount(false);
          else showGate(true);
          return;
        }
        // Item 2: a solo account skips the picker gate entirely.
        if (!accountId && state.accounts.length === 1) {
          state.activeAccountId = state.accounts[0].id;
          renderAccounts();
          await selectAccount();
          return;
        }
        if (!accountId) {
          clearProfile();
          renderAccounts();
          showGate(false);
          setAccountControls(false);
          return;
        }
        setView("workspace");
        setAccountControls(Boolean(status.authenticated));
        if (!status.authenticated) return;
        if (loadData) {
            await Promise.allSettled([loadContacts(), loadGroups(), loadSchedules(), loadBotLogs()]);
        }
      } catch (err) {
        toastError(err);
      }
    }

    function setTarget(mid, label) {
      state.target = mid;
      $("targetInput").value = mid;
      $("chatTitle").textContent = label || mid || t("chat.title");
      $("targetLabel").textContent = label ? `${label} / ${mid}` : (mid || t("chat.no_target"));
      $("scheduleTarget").value = mid || "";
    }

    function itemButton(title, mid, meta) {
      const btn = document.createElement("button");
      btn.className = "item";
      btn.type = "button";
      const strong = document.createElement("strong");
      strong.textContent = title || t("chat.unknown");
      const mono = document.createElement("span");
      mono.className = "mono";
      mono.textContent = mid;
      const muted = document.createElement("span");
      muted.className = "muted";
      muted.textContent = meta || "";
      btn.append(strong, mono, muted);
      btn.addEventListener("click", () => {
        setTarget(mid, title);
        loadMessages().catch(toastError);
      });
      return btn;
    }

    function listLoading(id) {
      const list = $(id);
      list.replaceChildren();
      const row = document.createElement("div");
      row.className = "state-row";
      const s = document.createElement("span");
      s.className = "spinner";
      row.append(s, textSpan(t("common.loading")));
      list.appendChild(row);
    }

    function listEmpty(id, key) {
      const list = $(id);
      list.replaceChildren();
      list.appendChild(textSpan(t(key), "state-row"));
    }

    function listError(id, retryFn) {
      const list = $(id);
      list.replaceChildren();
      const row = document.createElement("div");
      row.className = "state-row error";
      row.append(textSpan(t("common.load_failed")));
      const btn = document.createElement("button");
      btn.textContent = t("common.retry");
      btn.addEventListener("click", () => retryFn().catch(toastError));
      row.appendChild(btn);
      list.appendChild(row);
    }

    async function loadContacts() {
      const accountId = requireAccount();
      if (!accountId) return;
      const btn = $("contactsRefreshButton");
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      listLoading("contactsList");
      try {
        const search = encodeURIComponent($("contactSearch").value.trim());
        const data = await api(`/api/contacts?${accountQuery(accountId)}&limit=250&search=${search}`);
        state.contacts = data.contacts || [];
        populateScheduleTarget();
        const list = $("contactsList");
        list.replaceChildren();
        for (const c of data.contacts || []) {
          list.appendChild(itemButton(c.name, c.mid, c.statusMessage || ""));
        }
        if (!list.children.length) listEmpty("contactsList", $("contactSearch").value.trim() ? "contacts.no_match" : "contacts.none");
      } catch (err) {
        listError("contactsList", loadContacts);
        toastError(err);
      } finally {
        btn.disabled = false;
        btn.setAttribute("aria-busy", "false");
        applyPermissions();
      }
    }

    async function loadGroups() {
      const accountId = requireAccount();
      if (!accountId) return;
      const btn = $("contactsRefreshButton");
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      listLoading("groupsList");
      try {
        const data = await api(`/api/groups?${accountQuery(accountId)}&limit=200`);
        state.groups = data.groups || [];
        populateScheduleTarget();
        const list = $("groupsList");
        list.replaceChildren();
        for (const g of data.groups || []) {
          list.appendChild(itemButton(g.name, g.mid, `${g.memberCount || 0}`));
        }
        if (!list.children.length) listEmpty("groupsList", "groups.none");
      } catch (err) {
        listError("groupsList", loadGroups);
        toastError(err);
      } finally {
        btn.disabled = false;
        btn.setAttribute("aria-busy", "false");
        applyPermissions();
      }
    }

    function targetValue() {
      return $("targetInput").value.trim() || state.target;
    }

    function fmtTime(ms) {
      if (!ms) return "";
      const d = new Date(Number(ms));
      if (isNaN(d.getTime())) return "";
      return d.toLocaleString(state.lang === "th" ? "th-TH" : "en-US", {
        day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
      });
    }

    function contentLabel(m) {
      if (m.text) return m.text;
      const map = {1: "content.image", 2: "content.video", 3: "content.audio", 7: "content.sticker", 13: "content.file", 14: "content.file", 16: "content.file", 18: "content.file"};
      const key = map[m.contentType];
      return key ? t(key) : t("content.unsupported");
    }

    async function loadMessages(opts) {
      const silent = !!(opts && opts.silent);
      const accountId = requireAccount();
      if (!accountId) return;
      const target = targetValue();
      if (!target) {
        if (!silent) toast(t("toast.select_target_first"), true);
        return;
      }
      setTarget(target, $("chatTitle").textContent === t("chat.title") ? "" : $("chatTitle").textContent);
      const btn = $("loadMessagesButton");
      const label = btn.textContent;
      if (!silent) {
        btn.disabled = true;
        btn.textContent = t("common.working");
        listLoading("messagesList");
      }
      const count = $("messageCount").value;
      try {
        const data = await api(`/api/messages?${accountQuery(accountId)}&chat_mid=${encodeURIComponent(target)}&count=${encodeURIComponent(count)}`);
        const list = $("messagesList");
        list.replaceChildren();
        const myMid = state.profile && state.profile.mid;
        for (const m of data.messages || []) {
          const div = document.createElement("div");
          const isMe = m.from === myMid;
          const ctype = m.contentType;
          const isImage = (ctype === 1 || ctype === "1") && m.id && !m.encrypted;
          const isSticker = (ctype === 7 || ctype === "7") && m.stickerId;
          div.className = "bubble" + (isMe ? " me" : "") + (isImage || isSticker ? " has-media" : "");
          const body = messageBody(m, accountId, isImage, isSticker);
          if (isMe) {
            div.append(body);
          } else {
            const by = document.createElement("div");
            by.className = "by";
            by.textContent = m.fromName || t("chat.unknown");
            div.append(by, body);
          }
          const at = fmtTime(m.createdTime);
          if (at) {
            const atEl = document.createElement("div");
            atEl.className = "at";
            atEl.textContent = at;
            div.appendChild(atEl);
          }
          list.appendChild(div);
        }
        if (!list.children.length) listEmpty("messagesList", "chat.empty");
        list.scrollTop = list.scrollHeight;
      } catch (err) {
        if (!silent) listError("messagesList", () => loadMessages());
        toastError(err);
      } finally {
        if (!silent) {
          btn.textContent = label;
          btn.disabled = false;
          applyPermissions();
        }
      }
    }

    async function sendText(encrypt) {
      if (state.sending) return;
      const accountId = requireAccount();
      if (!accountId) return;
      const to = targetValue();
      const text = $("messageText").value;
      if (!to || !text.trim()) {
        toast(t("toast.target_message_required"), true);
        return;
      }
      state.sending = true;
      const btn = encrypt ? $("sendEncryptedButton") : $("sendButton");
      const label = btn.textContent;
      $("sendButton").disabled = true;
      $("sendEncryptedButton").disabled = true;
      btn.textContent = t("common.working");
      try {
        await post("/api/send", {accountId, to, text, encrypt});
        toast(t("toast.sent"));
        $("messageText").value = "";
        await loadMessages();
      } catch (err) {
        toastError(err);
      } finally {
        state.sending = false;
        btn.textContent = label;
        setAccountControls(true);
      }
    }

    function fileToBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || "").split(",", 2)[1] || "");
        reader.onerror = () => reject(new Error("Could not read the file."));
        reader.readAsDataURL(file);
      });
    }

    async function handleMediaPick(ev, kind) {
      const input = ev.target;
      const file = input.files && input.files[0];
      input.value = "";
      if (!file || state.sending) return;
      const accountId = requireAccount();
      if (!accountId) return;
      const to = targetValue();
      if (!to) { toast(t("toast.target_message_required"), true); return; }
      state.sending = true;
      setAccountControls(false);
      toast(t("toast.sending_media"));
      try {
        const data = await fileToBase64(file);
        await post("/api/send-media", {accountId, to, filename: file.name, kind, data});
        toast(t("toast.sent"));
        await loadMessages();
      } catch (err) {
        toastError(err);
      } finally {
        state.sending = false;
        setAccountControls(true);
      }
    }

    const EMOJIS = ["😀","😁","😂","🤣","😊","😍","😘","😎","🤗","🤔","😅","😉","🙂","🙃","😴","😇","🥰","😋","😜","🤩","👍","👏","🙏","💪","👌","🔥","✨","🎉","❤️","💚","💙","💛","😢","😭","😡","😱","🥳","😆","🙌","👋"];
    let emojiBuilt = false;
    function buildEmojiPop() {
      const pop = $("emojiPop");
      for (const emoji of EMOJIS) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "emoji-item";
        b.textContent = emoji;
        b.addEventListener("click", () => {
          insertAtCursor($("messageText"), emoji);
          pop.classList.add("hidden");
          $("messageText").focus();
        });
        pop.appendChild(b);
      }
      emojiBuilt = true;
    }
    function toggleEmojiPop() {
      const pop = $("emojiPop");
      if (!emojiBuilt) buildEmojiPop();
      pop.classList.toggle("hidden");
    }
    function insertAtCursor(el, text) {
      const start = el.selectionStart == null ? el.value.length : el.selectionStart;
      const end = el.selectionEnd == null ? el.value.length : el.selectionEnd;
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      el.selectionStart = el.selectionEnd = start + text.length;
    }

    function mediaFallback(m) {
      const d = document.createElement("div");
      d.className = "msg-chip";
      d.textContent = contentLabel(m);
      return d;
    }

    function messageBody(m, accountId, isImage, isSticker) {
      if (isImage) {
        const img = document.createElement("img");
        img.className = "msg-media";
        img.loading = "lazy";
        img.alt = t("content.image");
        img.src = `/api/message-content?${accountQuery(accountId)}&message_id=${encodeURIComponent(m.id)}`;
        img.addEventListener("click", () => window.open(img.src, "_blank", "noopener"));
        img.addEventListener("error", () => img.replaceWith(mediaFallback(m)));
        return img;
      }
      if (isSticker) {
        const img = document.createElement("img");
        img.className = "msg-sticker";
        img.loading = "lazy";
        img.alt = t("content.sticker");
        img.src = "https://stickershop.line-scdn.net/stickershop/v1/sticker/" + encodeURIComponent(m.stickerId) + "/android/sticker.png";
        img.addEventListener("error", () => img.replaceWith(mediaFallback(m)));
        return img;
      }
      const ct = m.contentType;
      const isImgType = ct === 1 || ct === "1";
      const isFile = ct === 13 || ct === "13" || ct === 14 || ct === "14"
        || ct === 16 || ct === "16" || ct === 18 || ct === "18";
      if ((isImgType || isFile) && m.encrypted) {
        // Letter-Sealed (E2EE) media lives in OBS encrypted; we can't decrypt
        // it here, so show an honest lock placeholder instead of a broken image.
        const d = document.createElement("div");
        d.className = "msg-chip";
        d.textContent = isFile && m.fileName
          ? "🔒 " + m.fileName
          : t(isFile ? "chat.sealed_file" : "chat.sealed_image");
        return d;
      }
      if (isFile && m.id) {
        const a = document.createElement("a");
        a.className = "msg-file";
        a.href = `/api/message-content?${accountQuery(accountId)}&message_id=${encodeURIComponent(m.id)}`;
        a.setAttribute("download", m.fileName || "file");
        a.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M13 2v7h7"/></svg>';
        const span = document.createElement("span");
        span.textContent = m.fileName || t("content.file");
        a.appendChild(span);
        return a;
      }
      const d = document.createElement("div");
      d.textContent = contentLabel(m);
      return d;
    }

    function loginStateLabel(stateName) {
      const key = "login.state." + (stateName || "idle");
      return I18N[key] ? t(key) : (stateName || t("login.idle"));
    }

    function setLoginStep(step) {
      state.loginStep = step;
      document.querySelectorAll("[data-login-step]").forEach((el) => {
        el.classList.toggle("active", Number(el.dataset.loginStep) === step);
      });
      document.querySelectorAll("#loginSteps [data-step]").forEach((el) => {
        const value = Number(el.dataset.step);
        el.classList.toggle("active", value === step);
        el.classList.toggle("done", value < step);
      });
      const focusTarget = step === 1
        ? $("beginAddAccountButton")
        : step === 2
          ? $("qrBox")
          : step === 3
            ? $("loginPinReview")
            : $("loginDoneText");
      if (focusTarget && focusTarget.focus) focusTarget.focus();
    }

    function resetLoginUi() {
      $("qrBox").replaceChildren(textSpan(t("login.qr_placeholder"), "muted"));
      $("qrActions").hidden = true;
      $("qrLink").removeAttribute("href");
      $("qrLink").textContent = "";
      $("loginState").textContent = t("login.idle");
      $("loginPinReview").textContent = "----";
      $("loginDoneText").textContent = t("login.done_text");
    }

    function openAddAccount(reset = true) {
      clearInterval(state.loginTimer);
      $("beginAddAccountButton").disabled = false;
      // Item F: no accounts yet -> nowhere to go "Back" to; hide the button.
      $("cancelLoginButton").classList.toggle("hidden", state.accounts.length === 0);
      if (reset) {
        resetLoginUi();
        setLoginStep(1);
      } else {
        setLoginStep(state.loginStep || 1);
      }
      setAccountControls(false);
      setView("login");
    }

    async function cancelLogin() {
      clearInterval(state.loginTimer);
      $("beginAddAccountButton").disabled = false;
      try { await post("/api/login/cancel"); } catch (_) { /* best effort */ }
      if (state.activeAccountId) {
        setView("workspace");
        setAccountControls(true);
      } else if (state.accounts.length) {
        showGate(false);
        setAccountControls(false);
      } else {
        openAddAccount(false);
      }
    }

    async function startLogin() {
      $("beginAddAccountButton").disabled = true;
      $("qrBox").replaceChildren(textSpan(t("login.starting"), "muted"));
      setLoginStep(2);
      try {
        await post("/api/login/start", {waitSeconds: 180});
        pollLogin();
        clearInterval(state.loginTimer);
        state.loginTimer = setInterval(pollLogin, 1400);
      } catch (err) {
        $("beginAddAccountButton").disabled = false;
        setLoginStep(1);
        toastError(err);
      }
    }

    function renderQr(data) {
      const box = $("qrBox");
      if (data.qrSvg) {
        box.innerHTML = data.qrSvg;
        if (data.state === "qr") setLoginStep(2);
      } else if (data.qrUrl) {
        // Server has no qrcode lib: never a dead end — show instructions + copy.
        box.replaceChildren(textSpan(t("login.qr_instructions"), "muted"));
        if (data.state === "qr") setLoginStep(2);
      }
      const actions = $("qrActions");
      if (data.qrUrl) {
        actions.hidden = false;
        $("qrLink").href = data.qrUrl;
        $("qrLink").textContent = data.qrUrl;
      }
    }

    async function pollLogin() {
      try {
        const data = await api("/api/login/state");
        $("loginState").textContent = loginStateLabel(data.state);
        $("loginPinReview").textContent = data.pin || "----";
        renderQr(data);
        if (data.state === "pin") setLoginStep(3);
        if (data.state === "success") {
          clearInterval(state.loginTimer);
          $("beginAddAccountButton").disabled = false;
          state.activeAccountId = data.account && data.account.id ? data.account.id : null;
          if (data.account && !state.accounts.some((account) => account.id === data.account.id)) {
            state.accounts.push(data.account);
          }
          setLoginStep(4);
          const lbl = data.account && data.account.label ? data.account.label : t("login.line_account");
          $("loginDoneText").textContent = t("login.added_opening", {name: lbl});
          toast(t("toast.account_added"));
          renderAccounts();
          setTab("line");
          setView("workspace");
          setAccountControls(false);
          await refreshStatus(true);
        }
        if (data.state === "error") {
          clearInterval(state.loginTimer);
          $("beginAddAccountButton").disabled = false;
          setLoginStep(1);
          toast(data.error || t("errors.server_error"), true);
        }
      } catch (err) {
        clearInterval(state.loginTimer);
        $("beginAddAccountButton").disabled = false;
        setLoginStep(1);
        toastError(err);
      }
    }

    async function copyQrLink() {
      const url = $("qrLink").href;
      if (!url) return;
      try {
        await navigator.clipboard.writeText(url);
        toast(t("common.copied"));
      } catch (_) {
        const range = document.createRange();
        range.selectNode($("qrLink"));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        toast(t("common.copied"));
      }
    }

    async function findUser() {
      const accountId = requireAccount();
      if (!accountId) return;
      const userId = $("findUserInput").value.trim();
      if (!userId) return;
      const data = await api(`/api/find-user?${accountQuery(accountId)}&userid=${encodeURIComponent(userId)}`);
      $("toolOutput").textContent = JSON.stringify(data, null, 2);
    }

    async function callEndpoint() {
      const accountId = requireAccount();
      if (!accountId) return;
      const endpoint = $("endpointInput").value.trim();
      let args = [];
      try { args = JSON.parse($("endpointArgs").value || "[]"); }
      catch (err) { toast(t("toast.args_json"), true); return; }
      const data = await post("/api/call", {accountId, endpoint, args});
      $("toolOutput").textContent = JSON.stringify(data, null, 2);
    }

    function fmtEpoch(epoch) {
      if (!epoch) return "-";
      return fmtTime(epoch * 1000) || "-";
    }

    function syncScheduleMode() {
      const repeat = $("scheduleMode").value === "repeat";
      $("repeatFields").style.display = repeat ? "grid" : "none";
      $("runAtWrap").style.display = repeat ? "none" : "grid";
      const source = $("scheduleContentSource").value;
      $("scheduleTextLabel").style.display = source === "text" ? "grid" : "none";
      $("scheduleTextHelp").style.display = source === "text" ? "flex" : "none";
      // Message patterns drive the text OR the AI image prompt.
      $("patternRow").style.display = (source === "text" || source === "ai_image") ? "grid" : "none";
      $("scheduleImageLabel").style.display = source === "image" ? "grid" : "none";
      $("scheduleImageUploadRow").style.display = source === "image" ? "flex" : "none";
      $("aiPromptLabel").style.display = source === "ai_image" ? "grid" : "none";
      $("apiFields").style.display = source === "api" ? "grid" : "none";
      // Images (uploaded, URL, or AI-generated) can't be Letter-Sealed, so lock
      // encryption off for those content types.
      const isImage = source === "image" || source === "ai_image";
      const enc = $("scheduleEncrypt");
      if (isImage) { enc.value = "false"; }
      enc.disabled = isImage;
      updateTextPreview();
      updateScheduleSummary();
    }

    function previewPlaceholders(s) {
      if (!s || s.indexOf("{") < 0) return s;
      const pad = (n, w) => String(n).padStart(w, "0");
      const rnd = (a, b) => Math.floor(Math.random() * (b - a + 1)) + a;
      s = s.replace(/\{rand:(-?\d+)-(-?\d+)\}/g, (m, a, b) => {
        a = Number(a); b = Number(b); if (a > b) { const t = a; a = b; b = t; }
        return String(rnd(a, b));
      });
      s = s.replace(/\{1D\}/g, () => String(rnd(0, 9)));
      s = s.replace(/\{2D\}/g, () => pad(rnd(0, 99), 2));
      s = s.replace(/\{3D\}/g, () => pad(rnd(0, 999), 3));
      if (s.indexOf("{date") >= 0 || s.indexOf("{time}") >= 0) {
        const now = new Date();
        const d = `${pad(now.getDate(), 2)}/${pad(now.getMonth() + 1, 2)}/${now.getFullYear()}`;
        const tm = `${pad(now.getHours(), 2)}:${pad(now.getMinutes(), 2)}`;
        s = s.replace(/\{datetime\}/g, d + " " + tm).replace(/\{date\}/g, d).replace(/\{time\}/g, tm);
      }
      return s;
    }

    function updateTextPreview() {
      const el = $("scheduleTextPreview");
      if (!el) return;
      const raw = $("scheduleText").value;
      const show = $("scheduleContentSource").value === "text" && raw && raw.indexOf("{") >= 0;
      el.textContent = show ? t("scheduler.preview") + " " + previewPlaceholders(raw) : "";
    }

    function updateScheduleSummary() {
      const el = $("scheduleSummary");
      if (!el) return;
      const mode = $("scheduleMode").value;
      if (mode === "once") {
        const at = runAtValue();
        const parts = at.split("T");
        const pretty = at ? isoToDmy(parts[0]) + " " + (parts[1] || "") : "";
        el.textContent = at ? t("scheduler.summary_once", {at: pretty}) : t("scheduler.summary_once_empty");
        return;
      }
      const iv = $("scheduleInterval").value || "15";
      const ws = hmValue("scheduleWindowStart");
      const we = hmValue("scheduleWindowEnd");
      const max = $("scheduleMaxRuns").value.trim();
      el.textContent = t("scheduler.summary_repeat", {interval: iv, start: ws, end: we}) + (max ? " " + t("scheduler.summary_max", {n: max}) : "");
    }

    async function loadPatterns() {
      try {
        const data = await api("/api/patterns");
        state.patterns = data.patterns || [];
        state.patternCategories = data.categories || [];
        populatePatternCategoryControls();
        populatePatternList();
        renderPatternManageList();
        renderPatternCategoryManageList();
      } catch (err) { /* patterns are optional — ignore load errors */ }
    }

    function patternCategoryLabel(category) {
      if (!category) return t("patterns.general");
      return category.id === "general" ? t("patterns.general") : category.name;
    }

    function patternCategoryName(categoryId) {
      const category = (state.patternCategories || []).find((item) => item.id === categoryId);
      return patternCategoryLabel(category);
    }

    function fillPatternCategorySelect(select, includeAll = false) {
      if (!select) return;
      const current = select.value;
      select.replaceChildren();
      if (includeAll) {
        const all = document.createElement("option");
        all.value = "all";
        all.textContent = t("patterns.all");
        select.appendChild(all);
      }
      for (const category of state.patternCategories || []) {
        const option = document.createElement("option");
        option.value = category.id;
        option.textContent = patternCategoryLabel(category);
        select.appendChild(option);
      }
      const fallback = includeAll ? "all" : "general";
      select.value = Array.from(select.options).some((option) => option.value === current)
        ? current : fallback;
    }

    function populatePatternCategoryControls() {
      fillPatternCategorySelect($("patternCategoryFilter"), true);
      fillPatternCategorySelect($("newPatternCategory"), false);
      $("patternCategoryFilter").value = state.patternCategoryFilter || "all";
      if (!$("patternCategoryFilter").value) {
        state.patternCategoryFilter = "all";
        $("patternCategoryFilter").value = "all";
      }
    }

    function populatePatternList() {
      const box = $("patternList");
      if (!box) return;
      box.replaceChildren();
      const selected = new Set(state.selectedPatternIds || []);
      if (!(state.patterns || []).length) {
        box.appendChild(textSpan(t("scheduler.no_patterns"), "muted"));
        return;
      }
      let lastCategory = null;
      for (const p of state.patterns) {
        const categoryId = p.categoryId || "general";
        if (categoryId !== lastCategory) {
          const heading = document.createElement("span");
          heading.className = "pattern-category-heading";
          heading.textContent = patternCategoryName(categoryId);
          box.appendChild(heading);
          lastCategory = categoryId;
        }
        const row = document.createElement("label");
        row.className = "pattern-item";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = selected.has(p.id);
        cb.addEventListener("change", () => toggleSelectedPattern(p.id, cb.checked));
        const name = document.createElement("span");
        name.className = "pattern-name";
        name.textContent = p.name;
        name.title = p.text;
        row.append(cb, name);
        box.appendChild(row);
      }
    }

    function toggleSelectedPattern(id, on) {
      const s = new Set(state.selectedPatternIds || []);
      if (on) s.add(id); else s.delete(id);
      state.selectedPatternIds = [...s];
    }

    async function createPattern() {
      const name = $("newPatternName").value.trim();
      const text = $("newPatternText").value;
      const categoryId = $("newPatternCategory").value || "general";
      const accountId = selectedAccountId();
      if (!name) { toast(t("toast.pattern_name_required"), true); return; }
      if (!text.trim()) { toast(t("toast.message_required"), true); return; }
      await post("/api/patterns/create", {name, text, categoryId, accountId});
      $("newPatternName").value = "";
      $("newPatternText").value = "";
      await loadPatterns();
      await loadBotLogs();
      setPatternFormOpen(false);
      toast(t("toast.pattern_saved"));
    }

    function renderPatternManageList() {
      const box = $("patternManageList");
      if (!box) return;
      box.replaceChildren();
      const selectedCategory = state.patternCategoryFilter || "all";
      const patterns = (state.patterns || []).filter((pattern) =>
        selectedCategory === "all" || (pattern.categoryId || "general") === selectedCategory
      );
      $("patternManageCount").textContent = String(patterns.length);
      if (!patterns.length) {
        box.appendChild(textSpan(t("scheduler.no_patterns_manage"), "muted"));
        return;
      }
      for (const p of patterns) {
        const item = document.createElement("div");
        item.className = "item";
        const name = document.createElement("strong");
        name.textContent = p.name;
        const preview = document.createElement("span");
        preview.className = "muted";
        preview.textContent = p.text;
        const category = document.createElement("span");
        category.className = "pattern-category-badge";
        category.textContent = patternCategoryName(p.categoryId || "general");
        const actions = document.createElement("div");
        actions.className = "item-actions";
        const del = document.createElement("button");
        del.className = "danger";
        del.textContent = t("scheduler.delete");
        del.addEventListener("click", () => deletePattern(p.id).catch(toastError));
        actions.append(del);
        item.append(name, category, preview, actions);
        box.appendChild(item);
      }
    }

    function renderPatternCategoryManageList() {
      const box = $("patternCategoryManageList");
      if (!box) return;
      box.replaceChildren();
      const categories = state.patternCategories || [];
      $("patternCategoryCount").textContent = String(categories.length);
      for (const category of categories) {
        const item = document.createElement("div");
        item.className = "item";
        const name = document.createElement("strong");
        name.textContent = patternCategoryLabel(category);
        const meta = document.createElement("span");
        meta.className = "muted";
        const patternCount = (state.patterns || []).filter(
          (pattern) => (pattern.categoryId || "general") === category.id
        ).length;
        meta.textContent = `${patternCount} ${t("patterns.list_title")}`;
        const actions = document.createElement("div");
        actions.className = "item-actions";
        if (category.system) {
          const system = document.createElement("span");
          system.className = "pattern-category-badge";
          system.textContent = t("patterns.system");
          actions.appendChild(system);
        } else {
          const edit = document.createElement("button");
          edit.textContent = t("common.edit");
          edit.addEventListener("click", () => editPatternCategory(category).catch(toastError));
          const del = document.createElement("button");
          del.className = "danger";
          del.textContent = t("scheduler.delete");
          del.addEventListener("click", () => deletePatternCategory(category.id).catch(toastError));
          actions.append(edit, del);
        }
        item.append(name, meta, actions);
        box.appendChild(item);
      }
    }

    async function createPatternCategory() {
      const result = await openModal({
        title: t("patterns.add_category"),
        build: (body) => {
          const field = labeledInput(t("patterns.category_name"), "text");
          field.input.placeholder = t("patterns.category_name_ph");
          field.input.maxLength = 80;
          body.appendChild(field.label);
          return () => field.input.value.trim();
        },
        confirmKey: "common.add"
      });
      if (!result) return;
      const data = await post("/api/pattern-categories/create", {
        name: result,
        accountId: selectedAccountId()
      });
      state.patternCategoryFilter = data.category.id;
      await loadPatterns();
      await loadBotLogs();
      toast(t("patterns.category_created"));
    }

    async function editPatternCategory(category) {
      const result = await openModal({
        title: t("patterns.edit_category"),
        build: (body) => {
          const field = labeledInput(t("patterns.category_name"), "text");
          field.input.value = category.name || "";
          field.input.maxLength = 80;
          body.appendChild(field.label);
          return () => field.input.value.trim();
        },
        confirmKey: "common.save"
      });
      if (!result || result === category.name) return;
      await post("/api/pattern-categories/update", {
        id: category.id,
        name: result,
        accountId: selectedAccountId()
      });
      await loadPatterns();
      await loadBotLogs();
      toast(t("patterns.category_updated"));
    }

    async function deletePatternCategory(categoryId) {
      if (!categoryId || categoryId === "all" || categoryId === "general") return;
      if (!confirm(t("patterns.delete_category_confirm"))) return;
      await post("/api/pattern-categories/delete", {
        id: categoryId,
        accountId: selectedAccountId()
      });
      state.patternCategoryFilter = "all";
      await loadPatterns();
      await loadBotLogs();
      toast(t("patterns.category_deleted"));
    }

    function setPatternFormOpen(open) {
      $("patternFormSection").classList.toggle("hidden", !open);
      $("patternFormToggle").setAttribute("aria-expanded", String(open));
      if (open) $("newPatternName").focus({preventScroll: true});
    }

    function togglePatternForm() {
      setPatternFormOpen($("patternFormSection").classList.contains("hidden"));
    }

    function openPatternsPage() {
      setBotPage("patterns");
      loadPatterns();
    }

    function closePatternsPage() {
      setBotPage("schedules");
    }

    function openPatternCategoriesPage() {
      setBotPage("pattern-categories");
      loadPatterns();
    }

    function closePatternCategoriesPage() {
      setBotPage("patterns");
      loadPatterns();
    }

    async function deletePattern(id) {
      if (!id) return;
      if (!confirm(t("confirm.delete_pattern"))) return;
      await post("/api/patterns/delete", {id, accountId: selectedAccountId()});
      state.selectedPatternIds = (state.selectedPatternIds || []).filter((x) => x !== id);
      await loadPatterns();
      await loadBotLogs();
      toast(t("toast.pattern_deleted"));
    }

    function populateScheduleTarget() {
      const sel = $("scheduleTarget");
      if (!sel) return;
      const current = sel.value;
      sel.replaceChildren();
      const ph = document.createElement("option");
      ph.value = "";
      ph.textContent = t("scheduler.pick_target");
      sel.appendChild(ph);
      const tab = state.scheduleTargetTab || "people";
      const items = tab === "groups" ? (state.groups || []) : (state.contacts || []);
      for (const it of items) {
        const o = document.createElement("option");
        o.value = it.mid;
        o.textContent = it.name || it.mid;
        sel.appendChild(o);
      }
      if (current) sel.value = current;
    }

    function setScheduleTargetTab(tab) {
      state.scheduleTargetTab = tab;
      document.querySelectorAll("#scheduleTargetTabs .subtab").forEach((b) => {
        const on = b.dataset.ttab === tab;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      populateScheduleTarget();
    }

    function initTimeSelects() {
      const fill = (id, count) => {
        const sel = $(id);
        if (!sel || sel.children.length) return;
        for (let i = 0; i < count; i++) {
          const o = document.createElement("option");
          o.value = String(i).padStart(2, "0");
          o.textContent = o.value;
          sel.appendChild(o);
        }
      };
      fill("scheduleWindowStartH", 24);
      fill("scheduleWindowStartM", 60);
      fill("scheduleWindowEndH", 24);
      fill("scheduleWindowEndM", 60);
      fill("scheduleRunAtH", 24);
      fill("scheduleRunAtM", 60);
      setHm("scheduleWindowStart", "09:00");
      setHm("scheduleWindowEnd", "18:00");
    }

    function runAtValue() {
      const d = $("scheduleRunAtDate").value;
      if (!d) return "";
      return d + "T" + ($("scheduleRunAtH").value || "00") + ":" + ($("scheduleRunAtM").value || "00");
    }

    // Native <input type=date> stores yyyy-mm-dd; we mirror it into a text field
    // shown as dd/mm/yyyy (browser locale can't be forced on the native input).
    function isoToDmy(iso) {
      const p = String(iso || "").split("-");
      return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : "";
    }

    function syncDateDisplay(nativeId) {
      const text = $(nativeId + "Text");
      if (text) text.value = isoToDmy($(nativeId).value);
    }

    function setRunAt(iso) {
      const parts = String(iso || "").split("T");
      $("scheduleRunAtDate").value = parts[0] || "";
      syncDateDisplay("scheduleRunAtDate");
      const tm = (parts[1] || "").split(":");
      if ($("scheduleRunAtH")) $("scheduleRunAtH").value = (tm[0] || "00").padStart(2, "0");
      if ($("scheduleRunAtM")) $("scheduleRunAtM").value = (tm[1] || "00").padStart(2, "0");
    }

    function hmValue(base) {
      return ($(base + "H").value || "00") + ":" + ($(base + "M").value || "00");
    }

    function setHm(base, hhmm) {
      const parts = String(hhmm || "").split(":");
      if ($(base + "H")) $(base + "H").value = (parts[0] || "09").padStart(2, "0");
      if ($(base + "M")) $(base + "M").value = (parts[1] || "00").padStart(2, "0");
    }

    function resetScheduleForm() {
      state.editingScheduleId = null;
      state.selectedPatternIds = [];
      state.scheduleImageData = "";
      state.scheduleImageName = "";
      $("scheduleName").value = "";
      $("scheduleContentSource").value = "text";
      $("scheduleText").value = "";
      $("scheduleImageSource").value = "";
      $("scheduleImageName").textContent = "";
      $("scheduleApiMethod").value = "GET";
      $("scheduleApiUrl").value = "";
      $("scheduleApiBody").value = "";
      $("scheduleAiPrompt").value = "";
      $("scheduleMode").value = "once";
      $("scheduleEncrypt").value = "false";
      setHm("scheduleWindowStart", "09:00");
      setHm("scheduleWindowEnd", "18:00");
      $("scheduleActiveFrom").value = "";
      $("scheduleActiveUntil").value = "";
      syncDateDisplay("scheduleActiveFrom");
      syncDateDisplay("scheduleActiveUntil");
      $("scheduleInterval").value = "15";
      $("scheduleMaxRuns").value = "";
      setDefaultRunAt();
      setScheduleTargetTab("people");
      $("scheduleTarget").value = "";
      $("createScheduleButton").textContent = t("scheduler.create");
      $("scheduleEditorTitle").dataset.i18n = "scheduler.new_title";
      $("scheduleEditorTitle").textContent = t("scheduler.new_title");
      $("scheduleForm").classList.remove("hidden");
      loadPatterns();
      syncScheduleMode();
      updateTextPreview();
    }

    function openScheduleCreatePage() {
      resetScheduleForm();
      setBotPage("schedule");
      $("scheduleName").focus();
    }

    function editSchedule(job) {
      setBotPage("schedule");
      state.editingScheduleId = job.id;
      $("scheduleEditorTitle").dataset.i18n = "scheduler.edit_title";
      $("scheduleEditorTitle").textContent = t("scheduler.edit_title");
      $("scheduleName").value = job.name || "";
      $("scheduleContentSource").value = job.contentSource || "text";
      $("scheduleText").value = job.text || "";
      $("scheduleImageSource").value = job.imageSource || "";
      state.scheduleImageData = "";
      state.scheduleImageName = "";
      $("scheduleImageName").textContent = "";
      $("scheduleApiMethod").value = job.apiMethod || "GET";
      $("scheduleApiUrl").value = job.apiUrl || "";
      $("scheduleApiBody").value = job.apiBody || "";
      $("scheduleAiPrompt").value = job.aiPrompt || "";
      $("scheduleMode").value = job.mode || "once";
      $("scheduleEncrypt").value = job.encrypt ? "true" : "false";
      setRunAt(job.runAt);
      setHm("scheduleWindowStart", job.windowStart || "09:00");
      setHm("scheduleWindowEnd", job.windowEnd || "18:00");
      $("scheduleActiveFrom").value = job.activeFrom || "";
      $("scheduleActiveUntil").value = job.activeUntil || "";
      syncDateDisplay("scheduleActiveFrom");
      syncDateDisplay("scheduleActiveUntil");
      $("scheduleInterval").value = job.intervalMinutes || 15;
      $("scheduleMaxRuns").value = job.maxRuns ? String(job.maxRuns) : "";
      state.selectedPatternIds = Array.isArray(job.patternIds) ? job.patternIds.slice() : [];
      const isGroup = (state.groups || []).some((g) => g.mid === job.to);
      setScheduleTargetTab(isGroup ? "groups" : "people");
      const sel = $("scheduleTarget");
      if (job.to && ![...sel.options].some((o) => o.value === job.to)) {
        const o = document.createElement("option");
        o.value = job.to;
        o.textContent = job.to;
        sel.appendChild(o);
      }
      sel.value = job.to || "";
      $("createScheduleButton").textContent = t("scheduler.save_edit");
      $("scheduleForm").classList.remove("hidden");
      loadPatterns();
      syncScheduleMode();
      updateTextPreview();
      $("botScheduleEditor").scrollIntoView({behavior: "smooth", block: "start"});
    }

    function schedulePayload() {
      const maxRaw = $("scheduleMaxRuns").value.trim();
      return {
        name: $("scheduleName").value.trim() || t("scheduler.default_name"),
        accountId: requireAccount(),
        to: $("scheduleTarget").value.trim() || targetValue(),
        contentSource: $("scheduleContentSource").value,
        text: $("scheduleText").value,
        imageSource: $("scheduleImageSource").value.trim(),
        imageData: state.scheduleImageData || "",
        imageName: state.scheduleImageName || "",
        patternIds: state.selectedPatternIds || [],
        patternTexts: (state.patterns || []).filter((p) => (state.selectedPatternIds || []).includes(p.id)).map((p) => p.text),
        apiUrl: $("scheduleApiUrl").value.trim(),
        apiMethod: $("scheduleApiMethod").value,
        apiBody: $("scheduleApiBody").value,
        aiPrompt: $("scheduleAiPrompt").value,
        encrypt: $("scheduleEncrypt").value === "true",
        mode: $("scheduleMode").value,
        runAt: runAtValue(),
        windowStart: hmValue("scheduleWindowStart"),
        windowEnd: hmValue("scheduleWindowEnd"),
        activeFrom: $("scheduleActiveFrom").value,
        activeUntil: $("scheduleActiveUntil").value,
        intervalMinutes: Number($("scheduleInterval").value || 15),
        maxRuns: maxRaw === "" ? 0 : Number(maxRaw),
        enabled: true
      };
    }

    function sameData(previous, next) {
      return JSON.stringify(previous || []) === JSON.stringify(next || []);
    }

    async function loadSchedules({background = false} = {}) {
      const accountId = requireAccount();
      if (!accountId) return;
      if (state.scheduleLoading) return;
      state.scheduleLoading = true;
      try {
        const data = await api(`/api/schedules?${accountQuery(accountId)}`);
        const schedules = data.schedules || [];
        const schedulesChanged = !sameData(state.schedules, schedules);
        state.schedules = schedules;
        if (data.accounts && !sameData(state.accounts, data.accounts)) {
          state.accounts = data.accounts;
          renderAccounts();
        }
        if (!background || schedulesChanged) renderSchedules();
      } finally {
        state.scheduleLoading = false;
      }
    }

    function isStuckSchedule(job) {
      const status = String(job && job.status || "").toLowerCase();
      return Boolean(job && (job.running || status === "running" || status === "error" || job.lastError));
    }

    function updateClearStuckButton() {
      const btn = $("clearStuckSchedulesButton");
      if (!btn) return;
      const count = (state.schedules || []).filter(isStuckSchedule).length;
      btn.classList.toggle("hidden", count === 0);
      btn.disabled = count === 0 || !selectedAccountId() || !hasPermission("schedule");
      btn.textContent = count ? t("scheduler.clear_stuck_count", {n: count}) : t("scheduler.clear_stuck");
    }

    function renderSchedules() {
      $("scheduleCount").textContent = t("scheduler.jobs", {n: state.schedules.length});
      updateClearStuckButton();
      const list = $("schedulesList");
      if (!list) return;
      list.replaceChildren();
      for (const job of state.schedules) {
        const item = document.createElement("div");
        item.className = "item";

        const title = document.createElement("strong");
        title.textContent = job.name || t("scheduler.default_name");
        const target = document.createElement("span");
        target.className = "mono";
        target.textContent = job.to;
        const meta = document.createElement("span");
        meta.className = "muted";
        const src = t("scheduler.source_" + (job.contentSource || "text"));
        const mode = t("scheduler.mode_" + (job.mode || "once"));
        const rawStatus = String(job.status || "").toLowerCase();
        const status = job.running || rawStatus === "running"
          ? t("scheduler.running")
          : rawStatus === "error" || job.lastError
            ? t("scheduler.error")
            : job.enabled ? t("scheduler.enabled") : t("scheduler.paused");
        meta.textContent = `${src} · ${mode} · ${status} · ${t("scheduler.next")}: ${fmtEpoch(job.nextRunAt)}`;
        const detail = document.createElement("span");
        detail.className = "muted";
        detail.textContent = job.lastError
          ? `${t("scheduler.error")}: ${job.lastError}`
          : `${t("scheduler.sent")}: ${job.sentCount || 0}${job.maxRuns ? "/" + job.maxRuns : ""}`;

        const actions = document.createElement("div");
        actions.className = "item-actions";
        const toggle = document.createElement("button");
        toggle.textContent = job.enabled ? t("scheduler.pause") : t("scheduler.resume");
        toggle.addEventListener("click", () => toggleSchedule(job.id, !job.enabled).catch(toastError));
        const run = document.createElement("button");
        run.textContent = t("scheduler.run_now");
        run.addEventListener("click", () => runScheduleNow(job.id).catch(toastError));
        const del = document.createElement("button");
        del.className = "danger";
        del.textContent = t("scheduler.delete");
        del.addEventListener("click", () => deleteSchedule(job.id).catch(toastError));
        const edit = document.createElement("button");
        edit.textContent = t("scheduler.edit");
        edit.addEventListener("click", () => editSchedule(job));
        actions.append(edit, toggle, run, del);

        item.append(title, target, meta, detail, actions);
        list.appendChild(item);
      }
      if (!list.children.length) list.appendChild(textSpan(t("scheduler.none"), "muted"));
    }

    function botActionLabel(action) {
      const key = "botlog.action." + String(action || "");
      return I18N[key] ? t(key) : String(action || "-");
    }

    function compactLogValue(value, limit = 24) {
      const text = String(value || "").trim();
      if (text.length <= limit) return text;
      const head = Math.max(6, Math.floor((limit - 3) * 0.62));
      const tail = Math.max(4, limit - 3 - head);
      return text.slice(0, head) + "..." + text.slice(-tail);
    }

    function renderBotLogs({preserveScroll = false} = {}) {
      const count = (state.botLogs || []).length;
      $("botLogCount").textContent = t("botlog.entries", {n: count});
      const list = $("botLogList");
      if (!list) return;
      const previousTop = list.scrollTop;
      const previousHeight = list.scrollHeight;
      const wasAtTop = previousTop < 24;
      list.replaceChildren();
      for (const log of state.botLogs || []) {
        const item = document.createElement("div");
        item.className = "bot-log-item";
        item.setAttribute("role", "listitem");

        const main = document.createElement("div");
        main.className = "log-main";
        const line = document.createElement("div");
        line.className = "log-line";
        const ts = log.ts ? Number(log.ts) * 1000 : Date.parse(log.at || "");
        const time = document.createElement("time");
        time.className = "log-time";
        time.textContent = "[" + fmtTime(ts) + "]";
        if (Number.isFinite(ts)) time.dateTime = new Date(ts).toISOString();
        const status = document.createElement("span");
        status.className = "log-status " + (log.ok === false ? "warn" : "ok");
        status.textContent = log.ok === false ? "[ERR]" : "[OK]";
        const title = document.createElement("strong");
        title.className = "log-action";
        title.textContent = botActionLabel(log.action);
        const meta = document.createElement("span");
        meta.className = "log-meta";
        const parts = [];
        if (log.scheduleName) parts.push(log.scheduleName);
        if (log.target) parts.push(compactLogValue(log.target, 22));
        meta.textContent = parts.length ? "-- " + parts.filter(Boolean).join(" / ") : "";
        meta.title = parts.filter(Boolean).join(" / ");
        line.append(time, status, title);
        if (parts.length) line.appendChild(meta);
        main.appendChild(line);
        const promptDetail = ["content.ai.start", "content.ai.request"].includes(log.action) && log.data
          ? String(log.data.prompt || "").trim()
          : "";
        const logDetail = promptDetail || log.detail;
        if (logDetail) {
          const detail = document.createElement("pre");
          const showFullDetail = ["content.ai.start", "content.ai.request"].includes(log.action)
            || log.action === "content.ai.error";
          detail.className = "log-detail" + (showFullDetail ? " log-full-detail" : "");
          detail.textContent = logDetail;
          detail.title = logDetail;
          main.appendChild(detail);
        }
        item.appendChild(main);
        list.appendChild(item);
      }
      if (!list.children.length) list.appendChild(textSpan(t("botlog.none"), "terminal-empty"));
      if (preserveScroll) {
        const heightChange = list.scrollHeight - previousHeight;
        list.scrollTop = wasAtTop ? 0 : Math.max(0, previousTop + heightChange);
      }
    }

    async function loadBotLogs({background = false} = {}) {
      const accountId = selectedAccountId();
      if (!accountId) {
        state.botLogs = [];
        renderBotLogs();
        return;
      }
      if (state.botLogLoading) return;
      state.botLogLoading = true;
      try {
        const data = await api(`/api/bot/logs?${accountQuery(accountId)}&limit=200`);
        const logs = data.logs || [];
        const logsChanged = !sameData(state.botLogs, logs);
        state.botLogs = logs;
        if (!background || logsChanged) renderBotLogs({preserveScroll: background});
      } finally {
        state.botLogLoading = false;
      }
    }

    async function clearBotLogs() {
      const accountId = requireAccount();
      if (!accountId || !confirm(t("confirm.clear_bot_logs"))) return;
      const data = await post("/api/bot/logs/clear", {accountId});
      state.botLogs = data.logs || [];
      renderBotLogs();
      toast(t("toast.bot_logs_cleared"));
    }

    async function clearStuckSchedules() {
      const accountId = requireAccount();
      if (!accountId) return;
      const count = (state.schedules || []).filter(isStuckSchedule).length;
      if (!count) {
        toast(t("toast.no_stuck_jobs"));
        return;
      }
      const ok = await openModal({
        title: t("scheduler.clear_stuck_title"),
        danger: true,
        confirmKey: "scheduler.clear_stuck",
        build: (body) => {
          body.appendChild(textSpan(t("scheduler.clear_stuck_note", {n: count}), "muted"));
          return () => true;
        }
      });
      if (!ok) return;
      const btn = $("clearStuckSchedulesButton");
      btn.disabled = true;
      const data = await post("/api/schedules/clear-stuck", {accountId});
      state.schedules = data.schedules || [];
      renderSchedules();
      await loadBotLogs();
      toast(t("toast.stuck_jobs_cleared", {n: data.cleared || 0}));
    }

    async function createSchedule() {
      const payload = schedulePayload();
      if (!payload.accountId) return;
      if (!payload.to) { toast(t("toast.target_required"), true); return; }
      if (payload.contentSource === "text" && !payload.text) { toast(t("toast.message_required"), true); return; }
      if (payload.contentSource === "image" && !payload.imageSource && !payload.imageData) { toast(t("toast.image_required"), true); return; }
      if (payload.contentSource === "api" && !payload.apiUrl) { toast(t("toast.api_url_required"), true); return; }
      if (payload.contentSource === "ai_image" && !payload.aiPrompt.trim() && !(payload.patternTexts || []).length) { toast(t("toast.ai_prompt_required"), true); return; }
      if (payload.mode === "once" && !payload.runAt) { toast(t("toast.pick_time"), true); return; }
      if (state.editingScheduleId) {
        payload.id = state.editingScheduleId;
        await post("/api/schedules/update", payload);
      } else {
        await post("/api/schedules/create", payload);
      }
      state.editingScheduleId = null;
      state.selectedPatternIds = [];
      $("createScheduleButton").textContent = t("scheduler.create");
      populatePatternList();
      updateTextPreview();
      toast(t("toast.schedule_created"));
      await loadSchedules();
      await loadBotLogs();
      setBotPage("schedules");
    }

    // ---- AI Settings (multi-provider) ----------------------------------
    function renderAiSettings(cfg) {
      cfg = cfg || {};
      if (!cfg.providers) cfg.providers = {};
      state.aiSettings = cfg;
      // Follow the saved active provider unless the user is mid-edit on one.
      if (!state.aiProvider || !cfg.providers[state.aiProvider]) {
        state.aiProvider = cfg.provider || "google";
      }
      const provSel = $("aiProvider");
      provSel.replaceChildren();
      for (const name of Object.keys(cfg.providers)) {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = cfg.providers[name].label || name;
        provSel.appendChild(o);
      }
      provSel.value = state.aiProvider;
      renderAiProvider();
    }

    function renderAiProvider() {
      const cfg = state.aiSettings || {};
      const name = state.aiProvider || cfg.provider || "google";
      const p = (cfg.providers || {})[name] || {};
      const isNano = name === "nanobananaapi";
      const isFal = name === "fal";
      const configured = !!p.hasApiKey;
      setPill("aiStatusPill", configured ? t("ai.status_on") : t("ai.status_off"), configured ? "ok" : "warn");
      $("aiModelLabel").textContent = t("ai.lbl_model");
      const sel = $("aiModel");
      sel.replaceChildren();
      const models = (p.models && p.models.length) ? p.models : [p.defaultModel || ""];
      for (const m of models) {
        const o = document.createElement("option");
        o.value = m;
        const label = (p.modelLabels && p.modelLabels[m]) ? p.modelLabels[m] : m;
        const price = (p.modelPrices && p.modelPrices[m]) ? p.modelPrices[m] : "";
        o.textContent = price ? label + " · " + price : label;
        sel.appendChild(o);
      }
      sel.value = p.model || p.defaultModel || models[0];
      updateAiModelPrice();
      const aspectRow = $("aiAspectRatioRow");
      const usesImageSize = !!(p.aspectRatios && p.aspectRatios.length);
      aspectRow.classList.toggle("hidden", !usesImageSize);
      const aspectSel = $("aiAspectRatio");
      aspectSel.replaceChildren();
      if (usesImageSize) {
        const ratios = (p.aspectRatios && p.aspectRatios.length) ? p.aspectRatios : [p.defaultAspectRatio || "auto"];
        for (const r of ratios) {
          const o = document.createElement("option");
          o.value = r;
          o.textContent = (p.aspectRatioLabels && p.aspectRatioLabels[r])
            ? p.aspectRatioLabels[r]
            : r === "auto" ? t("ai.size_auto") : r;
          aspectSel.appendChild(o);
        }
        aspectSel.value = p.aspectRatio || p.defaultAspectRatio || ratios[0];
      }
      $("aiApiKey").value = "";
      $("aiApiKeyCurrent").textContent = configured
        ? t("ai.current_key") + " " + (p.apiKeyPreview || "")
        : t("ai.no_key");
      $("aiKeyHint").textContent = isNano
        ? t("ai.hint_nano")
        : isFal ? t("ai.hint_fal") : t("ai.hint_google");
    }

    function updateAiModelPrice() {
      const cfg = state.aiSettings || {};
      const p = (cfg.providers || {})[state.aiProvider || cfg.provider || "google"] || {};
      const price = (p.modelPrices || {})[$("aiModel").value] || "";
      $("aiModelPrice").textContent = price ? price + " · " + t("ai.price_note") : "";
    }

    async function loadAiSettings() {
      try {
        renderAiSettings(await api("/api/ai/settings"));
      } catch (err) { toastError(err); }
    }

    async function saveAiSettings() {
      const body = {provider: state.aiProvider, model: $("aiModel").value};
      if (!$("aiAspectRatioRow").classList.contains("hidden")) {
        body.aspectRatio = $("aiAspectRatio").value;
      }
      const key = $("aiApiKey").value.trim();
      if (key) body.apiKey = key;
      try {
        const cfg = await post("/api/ai/settings", body);
        state.aiProvider = cfg.provider;
        renderAiSettings(cfg);
        toast(t("toast.ai_saved"));
      } catch (err) { toastError(err); }
    }

    async function clearAiKey() {
      if (!confirm(t("confirm.ai_clear"))) return;
      try {
        renderAiSettings(await post("/api/ai/settings", {provider: state.aiProvider, clearApiKey: true}));
        toast(t("toast.ai_cleared"));
      } catch (err) { toastError(err); }
    }

    async function testAiImage() {
      const prompt = $("aiTestPrompt").value.trim();
      if (!prompt) { toast(t("toast.ai_prompt_required"), true); return; }
      const status = $("aiTestStatus");
      const preview = $("aiTestPreview");
      status.textContent = t("ai.generating");
      $("aiTestButton").disabled = true;
      try {
        const res = await post("/api/ai/generate", {prompt});
        preview.replaceChildren();
        const img = document.createElement("img");
        img.src = "data:" + (res.mime || "image/png") + ";base64," + res.image;
        img.alt = prompt;
        preview.appendChild(img);
        preview.classList.remove("hidden");
        status.textContent = t("ai.done");
      } catch (err) {
        status.textContent = "";
        toastError(err);
      } finally {
        $("aiTestButton").disabled = false;
      }
    }

    async function activateAiProvider() {
      const previous = (state.aiSettings && state.aiSettings.provider) || "google";
      state.aiProvider = $("aiProvider").value;
      renderAiProvider();
      $("aiProvider").disabled = true;
      $("aiProvider").setAttribute("aria-busy", "true");
      $("aiTestButton").disabled = true;
      try {
        const cfg = await post("/api/ai/settings", {provider: state.aiProvider});
        state.aiProvider = cfg.provider;
        renderAiSettings(cfg);
        toast(t("toast.ai_provider_active"));
      } catch (err) {
        state.aiProvider = previous;
        renderAiSettings(state.aiSettings);
        toastError(err);
      } finally {
        $("aiProvider").disabled = false;
        $("aiProvider").setAttribute("aria-busy", "false");
        $("aiTestButton").disabled = false;
        applyPermissions();
      }
    }

    function toggleAiKeyVisibility() {
      const input = $("aiApiKey");
      const showing = input.type === "password";
      input.type = showing ? "text" : "password";
      $("aiApiKeyToggle").textContent = showing ? t("ai.hide") : t("ai.reveal");
    }

    async function toggleSchedule(id, enabled) {
      const accountId = requireAccount();
      if (!accountId) return;
      await post("/api/schedules/toggle", {id, enabled, accountId});
      await loadSchedules();
      await loadBotLogs();
    }

    async function runScheduleNow(id) {
      const accountId = requireAccount();
      if (!accountId) return;
      if (!confirm(t("confirm.run_now"))) return;
      await post("/api/schedules/run-now", {id, accountId});
      toast(t("toast.schedule_sent"));
      await loadSchedules();
      await loadBotLogs();
    }

    async function deleteSchedule(id) {
      const accountId = requireAccount();
      if (!accountId || !confirm(t("confirm.delete_schedule"))) return;
      await post("/api/schedules/delete", {id, accountId});
      await loadSchedules();
      await loadBotLogs();
    }

    function setDefaultRunAt() {
      const d = new Date(Date.now() + 5 * 60 * 1000);
      d.setSeconds(0, 0);
      const pad = (n) => String(n).padStart(2, "0");
      setRunAt(
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
        `T${pad(d.getHours())}:${pad(d.getMinutes())}`
      );
    }

    function labeledInput(text, type) {
      const label = document.createElement("label");
      label.append(textSpan(text));
      const input = document.createElement("input");
      input.type = type || "text";
      label.append(input);
      return {label, input};
    }

    function openModal({title, build, confirmKey = "common.confirm", cancelKey = "common.cancel", danger = false}) {
      return new Promise((resolve) => {
        const overlay = $("modalOverlay");
        $("modalTitle").textContent = title;
        const bodyEl = $("modalBody");
        bodyEl.replaceChildren();
        const getResult = build ? build(bodyEl) : () => true;
        const okBtn = $("modalConfirm");
        const cancelBtn = $("modalCancel");
        okBtn.textContent = t(confirmKey);
        cancelBtn.textContent = t(cancelKey);
        okBtn.className = "primary" + (danger ? " danger" : "");
        overlay.classList.remove("hidden");
        const onKey = (ev) => { if (ev.key === "Escape") done(null); };
        function done(val) {
          overlay.classList.add("hidden");
          okBtn.onclick = null;
          cancelBtn.onclick = null;
          overlay.onclick = null;
          document.removeEventListener("keydown", onKey);
          resolve(val);
        }
        okBtn.onclick = () => done(getResult ? getResult() : true);
        cancelBtn.onclick = () => done(null);
        overlay.onclick = (ev) => { if (ev.target === overlay) done(null); };
        document.addEventListener("keydown", onKey);
        const firstInput = bodyEl.querySelector("input, select, textarea");
        if (firstInput) firstInput.focus();
      });
    }

    // ---- wiring --------------------------------------------------------
    $("authForm").addEventListener("submit", (ev) => {
      ev.preventDefault();
      submitWebAuth().catch(toastError);
    });
    $("authModeToggle").addEventListener("click", () => {
      setAuthMode(authMode() === "register" ? "login" : "register");
    });
    $("toastClose").addEventListener("click", hideToast);
    $("langToggle").addEventListener("click", () => setLang(state.lang === "th" ? "en" : "th"));
    $("webLogoutButton").addEventListener("click", () => webLogout().catch(toastError));
    $("homeLogoButton").addEventListener("click", () => goHome().catch(toastError));
    $("refreshAllButton").addEventListener("click", () => refreshStatus(true));
    $("accountSelect").addEventListener("change", () => selectAccount().catch(toastError));
    $("settingsButton").addEventListener("click", toggleSettingsMenu);
    $("changePasswordMenuButton").addEventListener("click", () => openSettings("password"));
    $("accountManagementMenuButton").addEventListener("click", () => openSettings("accounts"));
    $("userManagementMenuButton").addEventListener("click", () => openSettings("users"));
    $("settingsBackButton").addEventListener("click", () => refreshStatus(false));
    $("passwordTabButton").addEventListener("click", () => setSettingsPane("password"));
    $("accountsTabButton").addEventListener("click", () => setSettingsPane("accounts"));
    $("usersTabButton").addEventListener("click", () => setSettingsPane("users"));
    $("changePasswordButton").addEventListener("click", () => changePassword().catch(toastError));
    $("secureButton").addEventListener("click", () => secureWithPassword().catch(toastError));
    $("createUserButton").addEventListener("click", () => createUser().catch(toastError));
    $("addAccountButton").addEventListener("click", () => openAddAccount());
    $("beginAddAccountButton").addEventListener("click", () => startLogin().catch(toastError));
    $("cancelLoginButton").addEventListener("click", () => cancelLogin().catch(toastError));
    $("qrCopyButton").addEventListener("click", () => copyQrLink());
    $("loadContactsButton").addEventListener("click", () => {
      setContactsSubtab("people");
      loadContacts().catch(toastError);
    });
    $("contactSearchButton").addEventListener("click", () => loadContacts().catch(toastError));
    $("contactSearch").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") loadContacts().catch(toastError);
    });
    $("loadGroupsButton").addEventListener("click", () => {
      setContactsSubtab("groups");
      loadGroups().catch(toastError);
    });
    $("contactsRefreshButton").addEventListener("click", () => {
      (state.contactsSubtab === "groups" ? loadGroups() : loadContacts()).catch(toastError);
    });
    document.querySelectorAll(".tabbar .tab").forEach((btn) => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });
    $("loadMessagesButton").addEventListener("click", () => loadMessages().catch(toastError));
    $("reloadMessagesButton").addEventListener("click", () => loadMessages().catch(toastError));
    $("sendButton").addEventListener("click", () => sendText(false).catch(toastError));
    $("sendEncryptedButton").addEventListener("click", () => sendText(true).catch(toastError));
    $("imageButton").addEventListener("click", () => $("imageFileInput").click());
    $("attachButton").addEventListener("click", () => $("fileFileInput").click());
    $("imageFileInput").addEventListener("change", (ev) => handleMediaPick(ev, "image"));
    $("fileFileInput").addEventListener("change", (ev) => handleMediaPick(ev, "file"));
    $("emojiButton").addEventListener("click", toggleEmojiPop);
    $("messageText").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        sendText(false).catch(toastError);
      }
    });
    $("findUserButton").addEventListener("click", () => findUser().catch(toastError));
    $("callEndpointButton").addEventListener("click", () => callEndpoint().catch(toastError));
    document.querySelectorAll("[data-bot-page-target]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const page = btn.dataset.botPageTarget;
        if (page === "schedule") openScheduleCreatePage();
        else setBotPage(page);
      });
    });
    $("scheduleFormToggle").addEventListener("click", openScheduleCreatePage);
    $("openBotLogsButton").addEventListener("click", () => setBotPage("logs"));
    $("cancelScheduleFormButton").addEventListener("click", () => {
      state.editingScheduleId = null;
      $("createScheduleButton").textContent = t("scheduler.create");
      setBotPage("schedules");
    });
    $("scheduleBackButton").addEventListener("click", () => setBotPage("schedules"));
    $("scheduleMode").addEventListener("change", syncScheduleMode);
    $("scheduleContentSource").addEventListener("change", syncScheduleMode);
    $("aiProvider").addEventListener("change", () => activateAiProvider().catch(toastError));
    $("aiModel").addEventListener("change", updateAiModelPrice);
    $("aiSaveButton").addEventListener("click", () => saveAiSettings().catch(toastError));
    $("aiClearButton").addEventListener("click", () => clearAiKey().catch(toastError));
    $("aiTestButton").addEventListener("click", () => testAiImage().catch(toastError));
    $("aiApiKeyToggle").addEventListener("click", toggleAiKeyVisibility);
    initTimeSelects();
    ["scheduleRunAtDate", "scheduleRunAtH", "scheduleRunAtM", "scheduleInterval", "scheduleWindowStartH", "scheduleWindowStartM", "scheduleWindowEndH", "scheduleWindowEndM", "scheduleMaxRuns"].forEach((id) => {
      $(id).addEventListener("input", updateScheduleSummary);
    });
    $("createScheduleButton").addEventListener("click", () => createSchedule().catch(toastError));
    $("clearStuckSchedulesButton").addEventListener("click", () => clearStuckSchedules().catch(toastError));
    $("scheduleText").addEventListener("input", updateTextPreview);
    $("patternFormToggle").addEventListener("click", togglePatternForm);
    $("cancelPatternFormButton").addEventListener("click", () => setPatternFormOpen(false));
    $("openPatternsButton").addEventListener("click", openPatternsPage);
    $("closePatternsButton").addEventListener("click", closePatternsPage);
    $("openPatternCategoriesButton").addEventListener("click", openPatternCategoriesPage);
    $("closePatternCategoriesButton").addEventListener("click", closePatternCategoriesPage);
    $("createPatternButton").addEventListener("click", () => createPattern().catch(toastError));
    $("addPatternCategoryButton").addEventListener("click", () => createPatternCategory().catch(toastError));
    $("patternCategoryFilter").addEventListener("change", () => {
      state.patternCategoryFilter = $("patternCategoryFilter").value || "all";
      populatePatternCategoryControls();
      renderPatternManageList();
    });
    $("botLogRefreshButton").addEventListener("click", () => loadBotLogs().catch(toastError));
    $("botLogClearButton").addEventListener("click", () => clearBotLogs().catch(toastError));
    $("patternTextHelp").addEventListener("click", (ev) => {
      const chip = ev.target.closest(".ph-chip");
      if (!chip) return;
      insertAtCursor($("newPatternText"), chip.dataset.ph);
      $("newPatternText").focus();
    });
    document.querySelectorAll("#scheduleTargetTabs .subtab").forEach((b) => {
      b.addEventListener("click", () => setScheduleTargetTab(b.dataset.ttab));
    });
    $("scheduleTextHelp").addEventListener("click", (ev) => {
      const chip = ev.target.closest(".ph-chip");
      if (!chip) return;
      insertAtCursor($("scheduleText"), chip.dataset.ph);
      $("scheduleText").focus();
      updateTextPreview();
    });
    $("scheduleImageUploadButton").addEventListener("click", () => $("scheduleImageInput").click());
    $("scheduleImageInput").addEventListener("change", async (ev) => {
      const file = ev.target.files && ev.target.files[0];
      ev.target.value = "";
      if (!file) return;
      try {
        state.scheduleImageData = await fileToBase64(file);
        state.scheduleImageName = file.name;
        $("scheduleImageName").textContent = file.name;
      } catch (err) { toastError(err); }
    });
    ["scheduleRunAtDate", "scheduleActiveFrom", "scheduleActiveUntil"].forEach((id) => {
      const native = $(id);
      if (!native) return;
      const openPicker = () => { try { if (native.showPicker) native.showPicker(); } catch (e) {} };
      native.addEventListener("click", openPicker);
      native.addEventListener("change", () => { syncDateDisplay(id); updateScheduleSummary(); });
      const text = $(id + "Text");
      if (text) text.addEventListener("click", openPicker);
      syncDateDisplay(id);
    });
    document.addEventListener("click", (ev) => {
      if (!$("settingsMenu").contains(ev.target) && !$("settingsButton").contains(ev.target)) {
        closeSettingsMenu();
      }
    });

    // Poll the open chat every ~10s while the tab is visible (item 12).
    setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (!state.activeAccountId || !state.target || state.sending) return;
      if ($("workspace").classList.contains("hidden")) return;
      loadMessages({silent: true}).catch(() => {});
    }, 10000);

    function refreshBotInBackground() {
      if (document.visibilityState !== "visible") return;
      if (state.tab !== "bot" || !selectedAccountId()) return;
      Promise.allSettled([
        loadSchedules({background: true}),
        loadBotLogs({background: true})
      ]);
    }

    // Keep both Bot lists live without showing an overlay or disturbing scroll.
    setInterval(refreshBotInBackground, BOT_BACKGROUND_REFRESH_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refreshBotInBackground();
    });

    state.lang = localStorage.getItem("okline.lang") === "en" ? "en" : "th";
    state.advanced = true;
    setTab(localStorage.getItem("okline.tab") || "line");
    setContactsSubtab("people");
    applyI18n();
    applyAdvanced();
    setLoginStep(1);
    setDefaultRunAt();
    syncScheduleMode();
    setAccountControls(false);
    refreshWebAuth();
  </script>
</body>
</html>
"""

GOD_HTML = r"""<!doctype html>
<html lang="th">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LinePassport God</title>
  <style>
    :root {
      --bg: #f3f6f5;
      --surface: #ffffff;
      --ink: #17211e;
      --muted: #68756f;
      --line: #d8e1de;
      --accent: #087f5b;
      --accent-soft: #e9f6f0;
      --danger: #b42318;
      --danger-soft: #fff0ee;
      --warning: #8a5a00;
      --warning-soft: #fff7df;
      --shadow: 0 18px 48px rgba(26, 45, 38, .1);
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font: 15px/1.5 "Segoe UI", Tahoma, sans-serif;
    }
    button, input, select { font: inherit; }
    button { cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: .55; }
    .hidden { display: none !important; }
    .auth-view {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background: linear-gradient(135deg, #eef7f3 0 48%, #f7f9f8 48% 100%);
    }
    .login-panel {
      width: min(100%, 400px);
      padding: 34px;
      background: var(--surface);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .brand-mark {
      width: 46px;
      height: 46px;
      display: grid;
      place-items: center;
      margin-bottom: 22px;
      color: #fff;
      background: var(--ink);
      font-size: 19px;
      font-weight: 800;
    }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 5px; font-size: 27px; letter-spacing: 0; }
    h2 { margin-bottom: 4px; font-size: 23px; letter-spacing: 0; }
    h3 { margin-bottom: 12px; font-size: 16px; letter-spacing: 0; }
    .muted { color: var(--muted); }
    .field { display: grid; gap: 7px; margin-top: 18px; font-weight: 600; }
    input, select {
      width: 100%;
      min-height: 44px;
      padding: 9px 12px;
      color: var(--ink);
      background: #fff;
      border: 1px solid #bdc9c5;
      border-radius: 4px;
      outline: none;
    }
    input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(8, 127, 91, .12); }
    .primary, .secondary, .danger-button, .icon-button {
      min-height: 40px;
      padding: 8px 14px;
      border: 1px solid transparent;
      border-radius: 4px;
      font-weight: 700;
    }
    .primary { color: #fff; background: var(--accent); }
    .secondary { color: var(--ink); background: #fff; border-color: var(--line); }
    .danger-button { color: var(--danger); background: #fff; border-color: #efb8b2; }
    .login-panel .primary { width: 100%; margin-top: 22px; }
    .error { min-height: 22px; margin: 12px 0 0; color: var(--danger); }
    .topbar {
      min-height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 0 28px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }
    .topbar-brand { display: flex; align-items: center; gap: 12px; }
    .topbar .brand-mark { width: 38px; height: 38px; margin: 0; font-size: 16px; }
    .topbar strong { display: block; font-size: 17px; }
    .topbar small { color: var(--muted); }
    .page { width: min(1280px, 100%); margin: 0 auto; padding: 28px; }
    .page-head {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 20px;
    }
    .summary { display: flex; gap: 8px; flex-wrap: wrap; }
    .summary span, .badge {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 3px 9px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 700;
      background: #edf1ef;
    }
    .badge.active { color: #087443; background: var(--accent-soft); }
    .badge.inactive { color: var(--danger); background: var(--danger-soft); }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-bottom: 0;
    }
    .search { max-width: 420px; }
    .user-table { background: var(--surface); border: 1px solid var(--line); }
    .user-row {
      display: grid;
      grid-template-columns: minmax(220px, 1.4fr) 150px 125px 185px;
      align-items: center;
      gap: 18px;
      min-height: 72px;
      padding: 12px 16px;
      border-top: 1px solid var(--line);
    }
    .user-row:first-child { border-top: 0; }
    .user-row.head { min-height: 42px; color: var(--muted); background: #f8faf9; font-size: 13px; font-weight: 700; }
    .identity { min-width: 0; }
    .identity strong, .identity span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .identity span { color: var(--muted); font-size: 13px; }
    .actions { display: flex; justify-content: flex-end; gap: 7px; }
    .empty { padding: 58px 20px; text-align: center; color: var(--muted); }
    .detail-panel {
      position: fixed;
      inset: 0 0 0 auto;
      z-index: 20;
      width: min(560px, 100%);
      overflow: auto;
      padding: 24px;
      background: var(--surface);
      border-left: 1px solid var(--line);
      box-shadow: -18px 0 48px rgba(26, 45, 38, .14);
    }
    .detail-head { display: flex; justify-content: space-between; gap: 18px; margin-bottom: 22px; }
    .icon-button { width: 40px; padding: 0; color: var(--ink); background: #fff; border-color: var(--line); font-size: 22px; }
    .detail-section { padding: 18px 0; border-top: 1px solid var(--line); }
    .detail-list { display: grid; gap: 9px; }
    .detail-item { padding: 11px 12px; background: #f6f8f7; border-left: 3px solid #b8c6c1; }
    .detail-item strong, .detail-item span { display: block; }
    .detail-item span { margin-top: 2px; color: var(--muted); font-size: 13px; }
    .pattern-text { margin-top: 7px; white-space: pre-wrap; word-break: break-word; color: var(--ink) !important; }
    .scrim { position: fixed; inset: 0; z-index: 19; background: rgba(11, 22, 18, .38); }
    dialog { width: min(480px, calc(100% - 30px)); padding: 0; border: 1px solid var(--line); border-radius: 6px; box-shadow: var(--shadow); }
    dialog::backdrop { background: rgba(11, 22, 18, .42); }
    .dialog-body { padding: 24px; }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 9px; padding: 14px 24px; background: #f7f9f8; border-top: 1px solid var(--line); }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .check-row { display: flex; align-items: center; gap: 9px; margin-top: 18px; font-weight: 600; }
    .check-row input { width: 18px; min-height: 18px; }
    .toast { position: fixed; right: 20px; bottom: 20px; z-index: 50; max-width: 420px; padding: 13px 16px; color: #fff; background: var(--ink); box-shadow: var(--shadow); }
    @media (max-width: 760px) {
      .topbar { padding: 0 16px; }
      .page { padding: 20px 14px; }
      .page-head { align-items: start; flex-direction: column; }
      .toolbar { align-items: stretch; flex-direction: column; }
      .search { max-width: none; }
      .user-row { grid-template-columns: 1fr auto; gap: 10px; }
      .user-row.head, .user-role, .user-status { display: none; }
      .actions { grid-column: 1 / -1; justify-content: flex-start; }
      .two-col { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <section class="auth-view" id="loginView">
    <form class="login-panel" id="loginForm">
      <div class="brand-mark">G</div>
      <h1>LinePassport God</h1>
      <p class="muted">User Management</p>
      <label class="field">God login<input id="godUsername" autocomplete="username" required></label>
      <label class="field">รหัสผ่าน<input id="godPassword" type="password" autocomplete="current-password" required></label>
      <button class="primary" id="loginButton" type="submit">เข้าสู่ระบบ</button>
      <p class="error" id="loginError" role="alert"></p>
    </form>
  </section>

  <div class="hidden" id="appView">
    <header class="topbar">
      <div class="topbar-brand"><div class="brand-mark">G</div><div><strong>LinePassport God</strong><small>User Management</small></div></div>
      <button class="secondary" id="logoutButton">ออกจากระบบ</button>
    </header>
    <main class="page">
      <div class="page-head">
        <div><h2>สมาชิก</h2><span class="muted">บัญชีที่สมัครใช้งาน LinePassport</span></div>
        <div class="summary"><span id="totalUsers">0 สมาชิก</span><span id="activeUsers">0 ใช้งานอยู่</span></div>
      </div>
      <div class="toolbar">
        <input class="search" id="searchInput" type="search" placeholder="ค้นหาอีเมลหรือชื่อสมาชิก" aria-label="ค้นหาสมาชิก">
        <button class="secondary" id="refreshButton">รีเฟรช</button>
      </div>
      <div class="user-table" id="userTable">
        <div class="user-row head"><span>สมาชิก</span><span>บทบาท</span><span>สถานะ</span><span></span></div>
        <div class="empty">กำลังโหลด...</div>
      </div>
    </main>
  </div>

  <div class="scrim hidden" id="detailScrim"></div>
  <aside class="detail-panel hidden" id="detailPanel" aria-label="รายละเอียดสมาชิก">
    <div class="detail-head"><div><h2 id="detailName"></h2><span class="muted" id="detailEmail"></span></div><button class="icon-button" id="closeDetailButton" aria-label="ปิด">&times;</button></div>
    <div id="detailContent"></div>
  </aside>

  <dialog id="editDialog">
    <form id="editForm">
      <div class="dialog-body">
        <h2>แก้ไขสมาชิก</h2>
        <input id="editUserId" type="hidden">
        <label class="field">อีเมล<input id="editEmail" type="email" required></label>
        <label class="field">ชื่อที่แสดง<input id="editDisplayName" required></label>
        <div class="two-col">
          <label class="field">บทบาท<select id="editRole"></select></label>
          <label class="field">รหัสผ่านใหม่<input id="editPassword" type="password" autocomplete="new-password" placeholder="ไม่เปลี่ยน"></label>
        </div>
        <label class="check-row"><input id="editActive" type="checkbox"> เปิดใช้งานบัญชี</label>
      </div>
      <div class="dialog-actions"><button class="secondary" type="button" data-close="editDialog">ยกเลิก</button><button class="primary" type="submit">บันทึก</button></div>
    </form>
  </dialog>

  <dialog id="deleteDialog">
    <div class="dialog-body"><h2>ลบสมาชิก</h2><p id="deleteMessage"></p><p class="muted">LINE Account, Pattern, งานบอท, Log และค่า AI ของสมาชิกจะถูกลบทั้งหมด</p></div>
    <div class="dialog-actions"><button class="secondary" data-close="deleteDialog">ยกเลิก</button><button class="danger-button" id="confirmDeleteButton">ลบสมาชิก</button></div>
  </dialog>
  <div class="toast hidden" id="toast" role="status"></div>

  <script>
    const state = {users: [], roles: {}, deleteUser: null};
    const $ = (id) => document.getElementById(id);

    const editPasswordConfirmLabel = document.createElement("label");
    editPasswordConfirmLabel.className = "field";
    editPasswordConfirmLabel.appendChild(document.createTextNode("ยืนยันรหัสผ่านใหม่"));
    const editPasswordConfirm = document.createElement("input");
    editPasswordConfirm.id = "editPasswordConfirm";
    editPasswordConfirm.type = "password";
    editPasswordConfirm.autocomplete = "new-password";
    editPasswordConfirm.placeholder = "ไม่เปลี่ยน";
    editPasswordConfirmLabel.appendChild(editPasswordConfirm);
    $("editPassword").closest(".two-col").appendChild(editPasswordConfirmLabel);

    async function api(path, options = {}) {
      const response = await fetch(path, {credentials: "same-origin", ...options, headers: {"Content-Type": "application/json", ...(options.headers || {})}});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401 && path !== "/api/god/login") showLogin();
        throw new Error(data.error || "เกิดข้อผิดพลาด");
      }
      return data;
    }

    function showLogin() {
      $("loginView").classList.remove("hidden");
      $("appView").classList.add("hidden");
      closeDetail();
    }

    function showApp() {
      $("loginView").classList.add("hidden");
      $("appView").classList.remove("hidden");
    }

    function toast(message) {
      $("toast").textContent = message;
      $("toast").classList.remove("hidden");
      clearTimeout(toast.timer);
      toast.timer = setTimeout(() => $("toast").classList.add("hidden"), 2600);
    }

    function roleLabel(role) {
      return (state.roles[role] && state.roles[role].label) || role;
    }

    function userMatches(user, query) {
      const value = `${user.email || user.username || ""} ${user.displayName || ""}`.toLowerCase();
      return value.includes(query.toLowerCase());
    }

    function makeButton(label, cls, handler) {
      const button = document.createElement("button");
      button.className = cls;
      button.textContent = label;
      button.addEventListener("click", handler);
      return button;
    }

    function renderUsers() {
      const table = $("userTable");
      table.querySelectorAll(".user-row:not(.head), .empty").forEach((node) => node.remove());
      const query = $("searchInput").value.trim();
      const users = state.users.filter((user) => userMatches(user, query));
      $("totalUsers").textContent = `${state.users.length} สมาชิก`;
      $("activeUsers").textContent = `${state.users.filter((user) => user.active).length} ใช้งานอยู่`;
      for (const user of users) {
        const row = document.createElement("div");
        row.className = "user-row";
        const identity = document.createElement("div");
        identity.className = "identity";
        const name = document.createElement("strong");
        name.textContent = user.displayName || user.email || user.username;
        const email = document.createElement("span");
        email.textContent = user.email || user.username;
        identity.append(name, email);
        const role = document.createElement("span");
        role.className = "user-role";
        role.textContent = roleLabel(user.role);
        const status = document.createElement("span");
        status.className = `badge user-status ${user.active ? "active" : "inactive"}`;
        status.textContent = user.active ? "ใช้งานอยู่" : "ปิดใช้งาน";
        const actions = document.createElement("div");
        actions.className = "actions";
        actions.append(
          makeButton("รายละเอียด", "secondary", () => openDetail(user)),
          makeButton("แก้ไข", "secondary", () => openEdit(user)),
          makeButton("ลบ", "danger-button", () => openDelete(user))
        );
        row.append(identity, role, status, actions);
        table.appendChild(row);
      }
      if (!users.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = query ? "ไม่พบสมาชิก" : "ยังไม่มีสมาชิก";
        table.appendChild(empty);
      }
    }

    async function loadUsers() {
      $("refreshButton").disabled = true;
      try {
        const data = await api("/api/god/users");
        state.users = data.users || [];
        state.roles = data.roles || {};
        renderRoleOptions();
        renderUsers();
      } finally {
        $("refreshButton").disabled = false;
      }
    }

    function renderRoleOptions() {
      const select = $("editRole");
      select.replaceChildren();
      for (const [value, meta] of Object.entries(state.roles)) {
        if (value === "god") continue;
        const option = document.createElement("option");
        option.value = value;
        option.textContent = meta.label || value;
        select.appendChild(option);
      }
    }

    function addDetailSection(root, title, items, render) {
      const section = document.createElement("section");
      section.className = "detail-section";
      const heading = document.createElement("h3");
      heading.textContent = `${title} (${items.length})`;
      const list = document.createElement("div");
      list.className = "detail-list";
      if (items.length) items.forEach((item) => list.appendChild(render(item)));
      else {
        const empty = document.createElement("span");
        empty.className = "muted";
        empty.textContent = "ไม่มีข้อมูล";
        list.appendChild(empty);
      }
      section.append(heading, list);
      root.appendChild(section);
    }

    function detailItem(title, meta, text = "") {
      const item = document.createElement("div");
      item.className = "detail-item";
      const strong = document.createElement("strong");
      strong.textContent = title || "-";
      const span = document.createElement("span");
      span.textContent = meta || "";
      item.append(strong, span);
      if (text) {
        const content = document.createElement("span");
        content.className = "pattern-text";
        content.textContent = text;
        item.appendChild(content);
      }
      return item;
    }

    async function openDetail(user) {
      $("detailName").textContent = user.displayName || user.email;
      $("detailEmail").textContent = user.email || user.username;
      $("detailContent").replaceChildren(detailItem("กำลังโหลด...", ""));
      $("detailScrim").classList.remove("hidden");
      $("detailPanel").classList.remove("hidden");
      try {
        const data = await api(`/api/god/users/detail?userId=${encodeURIComponent(user.id)}`);
        const root = $("detailContent");
        root.replaceChildren();
        addDetailSection(root, "LINE Account", data.accounts || [], (item) => detailItem(item.label || item.id, item.mid || item.id));
        addDetailSection(root, "Pattern", data.patterns || [], (item) => detailItem(item.name, item.categoryName || "General", item.text));
        addDetailSection(root, "งานบอท", data.schedules || [], (item) => detailItem(item.name, `${item.status || "-"} · ส่งแล้ว ${item.sentCount || 0} ครั้ง`));
        const ai = data.ai || {};
        addDetailSection(root, "AI", [ai], (item) => detailItem(item.providerLabel || item.provider || "ยังไม่ได้ตั้งค่า", item.modelLabel || item.model || "ไม่มีโมเดล", item.configured ? "ตั้ง API key แล้ว" : "ยังไม่ได้ตั้ง API key"));
      } catch (error) {
        $("detailContent").replaceChildren(detailItem("โหลดข้อมูลไม่สำเร็จ", error.message));
      }
    }

    function closeDetail() {
      $("detailPanel").classList.add("hidden");
      $("detailScrim").classList.add("hidden");
    }

    function openEdit(user) {
      $("editUserId").value = user.id;
      $("editEmail").value = user.email || user.username || "";
      $("editDisplayName").value = user.displayName || "";
      $("editRole").value = user.role;
      $("editPassword").value = "";
      $("editPasswordConfirm").value = "";
      $("editActive").checked = Boolean(user.active);
      $("editDialog").showModal();
    }

    function openDelete(user) {
      state.deleteUser = user;
      $("deleteMessage").textContent = `ยืนยันการลบ ${user.displayName || user.email} (${user.email || user.username})`;
      $("deleteDialog").showModal();
    }

    $("loginForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      $("loginError").textContent = "";
      $("loginButton").disabled = true;
      try {
        await api("/api/god/login", {method: "POST", body: JSON.stringify({username: $("godUsername").value.trim(), password: $("godPassword").value})});
        $("godPassword").value = "";
        showApp();
        await loadUsers();
      } catch (error) {
        $("loginError").textContent = error.message;
      } finally {
        $("loginButton").disabled = false;
      }
    });

    $("editForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if ($("editPassword").value !== $("editPasswordConfirm").value) {
        toast("รหัสผ่านใหม่ไม่ตรงกัน");
        $("editPasswordConfirm").focus();
        return;
      }
      const payload = {id: $("editUserId").value, email: $("editEmail").value.trim(), displayName: $("editDisplayName").value.trim(), role: $("editRole").value, active: $("editActive").checked};
      if ($("editPassword").value) payload.password = $("editPassword").value;
      try {
        await api("/api/god/users/update", {method: "POST", body: JSON.stringify(payload)});
        $("editDialog").close();
        toast("บันทึกข้อมูลสมาชิกแล้ว");
        await loadUsers();
      } catch (error) { toast(error.message); }
    });

    $("confirmDeleteButton").addEventListener("click", async () => {
      if (!state.deleteUser) return;
      $("confirmDeleteButton").disabled = true;
      try {
        await api("/api/god/users/delete", {method: "POST", body: JSON.stringify({id: state.deleteUser.id})});
        $("deleteDialog").close();
        closeDetail();
        toast("ลบสมาชิกและข้อมูลทั้งหมดแล้ว");
        state.deleteUser = null;
        await loadUsers();
      } catch (error) { toast(error.message); }
      finally { $("confirmDeleteButton").disabled = false; }
    });

    document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
    $("closeDetailButton").addEventListener("click", closeDetail);
    $("detailScrim").addEventListener("click", closeDetail);
    $("searchInput").addEventListener("input", renderUsers);
    $("refreshButton").addEventListener("click", () => loadUsers().catch((error) => toast(error.message)));
    $("logoutButton").addEventListener("click", async () => { await api("/api/god/logout", {method: "POST", body: "{}"}); showLogin(); });

    api("/api/god/status").then((data) => {
      if (!data.authenticated) return showLogin();
      showApp();
      return loadUsers();
    }).catch(showLogin);
  </script>
</body>
</html>
"""


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    tokens_file: str = "tokens.json"
    state_dir: str = ".okline"
    accounts_file: str = ".okline/accounts.json"
    accounts_dir: str = ".okline/accounts"
    schedules_file: str = ".okline/schedules.json"
    auth_file: str = ".okline/auth.json"
    database_url: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    show_secrets: bool = False


@dataclass
class WebResult:
    data: Any
    status: int = HTTPStatus.OK
    headers: list[tuple[str, str]] | None = None


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "auth_required",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    500: "server_error",
    502: "upstream_error",
}


def _status_code(status: int) -> str:
    return _STATUS_CODES.get(int(status), "error")


class WebError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        # A stable, machine-readable code the browser localises independently
        # of the human message.  Falls back to a status-derived default.
        self.code = code or _status_code(status)


ROLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "god": {
        "label": "God",
        "permissions": [
            "read",
            "send",
            "schedule",
            "tools",
            "manage_accounts",
            "manage_users",
            "change_password",
        ],
    },
    "admin": {
        "label": "Admin",
        "permissions": [
            "read",
            "send",
            "schedule",
            "tools",
            "manage_accounts",
            "change_password",
        ],
    },
    "member": {
        "label": "Member",
        "permissions": [
            "read",
            "send",
            "schedule",
            "tools",
            "manage_accounts",
            "change_password",
        ],
    },
    "operator": {
        "label": "Operator",
        "permissions": ["read", "send", "schedule", "tools", "change_password"],
    },
    "viewer": {
        "label": "Viewer",
        "permissions": ["read", "change_password"],
    },
}


def _now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _safe_slug(value: str, fallback: str = "account") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._").lower()
    return slug[:42] or fallback


def _read_json_file(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except ValueError:
        return default


def _write_json_file(path: str, value: Any) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class StateStore:
    def get(self, key: str, default: Any) -> Any:
        raise NotImplementedError

    def set(self, key: str, value: Any) -> None:
        raise NotImplementedError

    def label(self) -> str:
        raise NotImplementedError


class FileStateStore(StateStore):
    def __init__(self, config: WebConfig) -> None:
        self.config = config
        self.lock = threading.RLock()
        self.paths = {
            "accounts": config.accounts_file,
            "schedules": config.schedules_file,
            "auth": config.auth_file,
            "patterns": str(Path(config.schedules_file).parent / "patterns.json"),
            "ai_settings": str(Path(config.schedules_file).parent / "ai_settings.json"),
            "bot_logs": str(Path(config.schedules_file).parent / "bot_logs.json"),
        }

    def get(self, key: str, default: Any) -> Any:
        with self.lock:
            return _read_json_file(self.paths[key], default)

    def set(self, key: str, value: Any) -> None:
        with self.lock:
            _write_json_file(self.paths[key], value)

    def label(self) -> str:
        return "file"


class PostgresStateStore(StateStore):
    def __init__(self, database_url: str, config: WebConfig) -> None:
        self.database_url = database_url
        self._ensure_schema()
        self._migrate_legacy_files(config)

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise WebError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "PostgreSQL support requires psycopg. Run: pip install 'psycopg[binary]>=3.1'",
            ) from exc
        return psycopg.connect(self.database_url)

    def _ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS okline_web_state (
                  key text PRIMARY KEY,
                  value jsonb NOT NULL,
                  updated_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )

    def _migrate_legacy_files(self, config: WebConfig) -> None:
        legacy: dict[str, tuple[str, Any]] = {
            "accounts": (config.accounts_file, {"accounts": []}),
            "schedules": (config.schedules_file, {"schedules": []}),
            "auth": (config.auth_file, {}),
            "patterns": (str(Path(config.schedules_file).parent / "patterns.json"), {"patterns": []}),
            "ai_settings": (str(Path(config.schedules_file).parent / "ai_settings.json"), {}),
            "bot_logs": (str(Path(config.schedules_file).parent / "bot_logs.json"), {"logs": []}),
        }
        for key, (path, default) in legacy.items():
            if self.get(key, None) is not None or not os.path.exists(path):
                continue
            self.set(key, _read_json_file(path, default))

    def get(self, key: str, default: Any) -> Any:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM okline_web_state WHERE key = %s", (key,))
            row = cur.fetchone()
        if row is None:
            return default
        value = row[0]
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        raw = json.dumps(value, ensure_ascii=False, default=_json_default)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO okline_web_state (key, value, updated_at)
                VALUES (%s, %s::jsonb, now())
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (key, raw),
            )

    def label(self) -> str:
        return "postgresql"


def _make_state_store(config: WebConfig) -> StateStore:
    database_url = (
        config.database_url or os.getenv("OKLINE_DATABASE_URL") or os.getenv("DATABASE_URL")
    )
    if database_url:
        return PostgresStateStore(database_url, config)
    return FileStateStore(config)


class WebAuth:
    cookie_name = "okline_web_session"
    god_cookie_name = "okline_god_session"
    iterations = 240_000

    def __init__(self, store: StateStore | str) -> None:
        self.lock = threading.RLock()
        if isinstance(store, str):
            self.store: StateStore = FileStateStore(
                WebConfig(auth_file=store, accounts_file="", schedules_file="")
            )
        else:
            self.store = store
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        data = self.store.get("auth", {})
        if not isinstance(data, dict):
            data = {}
        if data.get("salt") and data.get("passwordHash"):
            data = {
                "users": [
                    {
                        "id": uuid.uuid4().hex,
                        "username": "admin",
                        "displayName": "Admin",
                        "role": "admin",
                        "salt": data["salt"],
                        "passwordHash": data["passwordHash"],
                        "accountIds": [],
                        "active": True,
                        "createdAt": data.get("createdAt") or _now_iso(),
                        "updatedAt": _now_iso(),
                    }
                ],
                "sessions": {},
                "createdAt": data.get("createdAt") or _now_iso(),
                "updatedAt": _now_iso(),
            }
            self._save(data)
        data.setdefault("users", [])
        data.setdefault("sessions", {})
        migrated = False
        for user in data["users"]:
            if not isinstance(user, dict) or user.get("email"):
                continue
            email = self._email_or_empty(str(user.get("username") or ""))
            if email:
                user["email"] = email
                user["username"] = email
                migrated = True
        if migrated:
            data["updatedAt"] = _now_iso()
            self._save(data)
        return data

    def _save(self, data: dict[str, Any] | None = None) -> None:
        if data is not None:
            self.data = data
        self.store.set("auth", self.data)

    def configured(self) -> bool:
        return any(self._is_active_user(user) for user in self.data.get("users", []))

    def simple_mode(self) -> bool:
        """True when the app is running unsecured with the auto-provisioned
        local user (no password prompt).  Cleared the moment a real user or a
        password is added via the Advanced panel."""
        return bool(self.data.get("simpleMode"))

    def ensure_simple_mode(self) -> str | None:
        """First run: silently provision a single local admin so a solo,
        non-technical user lands straight on QR login / chat instead of a
        "create admin" wall.  Returns a fresh session token when it just
        provisioned (so the caller can set the cookie), else ``None``."""
        with self.lock:
            if self.configured():
                return None
            password = secrets.token_urlsafe(24)
            user = self._new_user("local", password, "admin", [], "Local User")
            user["simple"] = True
            self.data = {
                "users": [user],
                "sessions": {},
                "simpleMode": True,
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
            }
            token = self.create_session(user["id"])
            self._save()
            return token

    def secure_with_password(self, username: str, password: str) -> None:
        """Graduate out of simple mode: give the local user a real
        username/password so future visitors must sign in."""
        username = self._normalize_email(username)
        self._validate_password(password)
        with self.lock:
            users = self.data.get("users", [])
            target = next((u for u in users if u.get("simple")), None) or (
                users[0] if users else None
            )
            if target is None:
                raise WebError(HTTPStatus.BAD_REQUEST, "No user to secure.")
            other = self._find_user_by_username(username)
            if other and other.get("id") != target.get("id"):
                raise WebError(HTTPStatus.CONFLICT, "Username already exists.")
            salt = secrets.token_hex(16)
            target["username"] = username
            target["email"] = username
            target["salt"] = salt
            target["passwordHash"] = self._hash_password(password, salt)
            target["role"] = "admin"
            target["active"] = True
            target.pop("simple", None)
            target["updatedAt"] = _now_iso()
            self.data.pop("simpleMode", None)
            self._save()

    def setup(self, username: str, password: str) -> str:
        username = self._normalize_email(username)
        self._validate_password(password)
        with self.lock:
            if self.configured():
                raise WebError(HTTPStatus.CONFLICT, "Web auth is already configured.")
            user = self._new_user(username, password, "admin", [], "Admin")
            self.data = {
                "users": [user],
                "sessions": {},
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
            }
            token = self.create_session(user["id"])
            self._save()
            return token

    def login(self, username: str, password: str) -> str:
        return self._login_for_portal(username, password, god_only=False)

    def login_god(self, username: str, password: str) -> str:
        return self._login_for_portal(username, password, god_only=True)

    def _login_for_portal(
        self, username: str, password: str, *, god_only: bool
    ) -> str:
        with self.lock:
            if not self.configured():
                raise WebError(HTTPStatus.BAD_REQUEST, "Web auth is not configured.")
            user = self._find_user_by_username(username)
            if user is None or not self._is_active_user(user):
                raise WebError(HTTPStatus.UNAUTHORIZED, "Invalid username or password.")
            is_god = self._is_god(user)
            if is_god != god_only:
                raise WebError(HTTPStatus.UNAUTHORIZED, "Invalid username or password.")
            expected = str(user.get("passwordHash") or "")
            salt = str(user.get("salt") or "")
            actual = self._hash_password(password or "", salt)
            if not hmac.compare_digest(expected, actual):
                raise WebError(HTTPStatus.UNAUTHORIZED, "Invalid username or password.")
            token = self.create_session(str(user["id"]))
            self._save()
            return token

    def register(self, username: str, password: str, display_name: str = "") -> str:
        username = self._normalize_email(username)
        self._validate_password(password)
        with self.lock:
            if not self.configured() or self.simple_mode():
                raise WebError(
                    HTTPStatus.BAD_REQUEST,
                    "Registration is available after LinePassport auth is secured.",
                    "register_unavailable",
                )
            if self._find_user_by_username(username):
                raise WebError(HTTPStatus.CONFLICT, "Email already exists.", "email_exists")
            user = self._new_user(username, password, "member", [], display_name or username)
            self.data.setdefault("users", []).append(user)
            token = self.create_session(str(user["id"]))
            self._save()
            return token

    def ensure_god(self, username: str, password: str) -> dict[str, Any]:
        username = self._normalize_username(username)
        self._validate_password(password)
        with self.lock:
            user = self._find_user_by_username(username)
            if user is None:
                user = self._new_user(username, password, "god", [], "God")
                self.data.setdefault("users", []).append(user)
            else:
                salt = secrets.token_hex(16)
                user["salt"] = salt
                user["passwordHash"] = self._hash_password(password, salt)
                user["role"] = "god"
                user["active"] = True
                user["displayName"] = user.get("displayName") or "God"
                user["updatedAt"] = _now_iso()
            self.data.pop("simpleMode", None)
            self._save()
            return self._public_user_strict(user)

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self.data.setdefault("sessions", {})[token] = {
            "userId": user_id,
            "expiresAt": time.time() + 12 * 60 * 60,
            "createdAt": _now_iso(),
        }
        return token

    def logout(
        self, cookie_header: str | None, *, cookie_name: str | None = None
    ) -> None:
        token = self._token_from_cookie(cookie_header, cookie_name=cookie_name)
        if token:
            with self.lock:
                self.data.setdefault("sessions", {}).pop(token, None)
                self._save()

    def is_authenticated(
        self, cookie_header: str | None, *, cookie_name: str | None = None
    ) -> bool:
        return self.current_user(cookie_header, cookie_name=cookie_name) is not None

    def current_user(
        self, cookie_header: str | None, *, cookie_name: str | None = None
    ) -> dict[str, Any] | None:
        token = self._token_from_cookie(cookie_header, cookie_name=cookie_name)
        if not token:
            return None
        now = time.time()
        with self.lock:
            session = self.data.setdefault("sessions", {}).get(token)
            if not isinstance(session, dict):
                return None
            if float(session.get("expiresAt") or 0) <= now:
                self.data["sessions"].pop(token, None)
                self._save()
                return None
            user = self._find_user(str(session.get("userId") or ""))
            if user is None or not self._is_active_user(user):
                self.data["sessions"].pop(token, None)
                self._save()
                return None
            session["expiresAt"] = now + 12 * 60 * 60
            self._save()
            return dict(user)

    def require_user(
        self, cookie_header: str | None, *, cookie_name: str | None = None
    ) -> dict[str, Any]:
        user = self.current_user(cookie_header, cookie_name=cookie_name)
        if user is None:
            raise WebError(
                HTTPStatus.UNAUTHORIZED, "Web authentication required.", "auth_required"
            )
        return user

    def status(
        self, cookie_header: str | None, *, cookie_name: str | None = None
    ) -> dict[str, Any]:
        user = self.current_user(cookie_header, cookie_name=cookie_name)
        return {
            "configured": self.configured(),
            "authenticated": user is not None,
            "simpleMode": self.simple_mode(),
            "user": self.public_user(user) if user else None,
            "roles": self.roles_payload(),
        }

    def has_permission(self, user: dict[str, Any] | None, permission: str) -> bool:
        if not user:
            return False
        role = str(user.get("role") or "")
        return permission in ROLE_DEFINITIONS.get(role, {}).get("permissions", [])

    def can_access_account(self, user: dict[str, Any] | None, account_id: str) -> bool:
        if not user or not account_id:
            return False
        if self._is_god(user):
            return True
        return account_id in set(user.get("accountIds") or [])

    def allowed_account_ids(self, user: dict[str, Any] | None) -> set[str] | None:
        if self._is_god(user):
            return None
        return set(user.get("accountIds") or []) if user else set()

    def grant_account_access(self, user_id: str, account_id: str) -> None:
        with self.lock:
            user = self._find_user(user_id)
            if user is None:
                raise WebError(HTTPStatus.NOT_FOUND, "User not found.")
            self._ensure_accounts_unassigned([account_id], exclude_user_id=user_id)
            account_ids = set(user.get("accountIds") or [])
            account_ids.add(account_id)
            user["accountIds"] = sorted(account_ids)
            user["updatedAt"] = _now_iso()
            self._save()

    def user_for_account(self, account_id: str) -> dict[str, Any] | None:
        for user in self.data.get("users", []):
            if isinstance(user, dict) and account_id in set(user.get("accountIds") or []):
                return dict(user)
        return None

    def require_permission(self, user: dict[str, Any] | None, permission: str) -> None:
        if not self.has_permission(user, permission):
            raise WebError(HTTPStatus.FORBIDDEN, "Permission denied.", "forbidden")

    def list_users(self) -> list[dict[str, Any]]:
        return [self._public_user_strict(user) for user in self.data.get("users", [])]

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        account_ids: list[str],
        display_name: str = "",
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        username = self._normalize_email(username)
        self._validate_password(password)
        self._validate_role(role)
        if account_ids:
            raise WebError(
                HTTPStatus.BAD_REQUEST,
                "Users must add their own LINE accounts.",
                "account_self_service_only",
            )
        if role == "god" and not self._is_god(actor):
            raise WebError(HTTPStatus.FORBIDDEN, "Only God can create God users.")
        with self.lock:
            self._ensure_accounts_unassigned(account_ids)
            if self._find_user_by_username(username):
                raise WebError(HTTPStatus.CONFLICT, "Email already exists.", "email_exists")
            user = self._new_user(
                username, password, role, account_ids, display_name or username
            )
            self.data.setdefault("users", []).append(user)
            # Adding a real user means multi-user access is now in play: leave
            # simple (unsecured) mode so future visitors are prompted to sign in.
            self.data.pop("simpleMode", None)
            self._save()
            return self._public_user_strict(user)

    def update_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        role: str | None = None,
        account_ids: list[str] | None = None,
        active: bool | None = None,
        password: str | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            user = self._find_user(user_id)
            if user is None:
                raise WebError(HTTPStatus.NOT_FOUND, "User not found.")
            actor_is_god = self._is_god(actor)
            if user.get("role") == "god" and not actor_is_god:
                raise WebError(HTTPStatus.FORBIDDEN, "Only God can manage God users.")
            if role == "god" and not actor_is_god:
                raise WebError(HTTPStatus.FORBIDDEN, "Only God can assign the God role.")
            if actor_is_god and user_id == str(actor.get("id") or ""):
                if role is not None and role != "god":
                    raise WebError(HTTPStatus.BAD_REQUEST, "God cannot demote itself.")
                if active is False:
                    raise WebError(HTTPStatus.BAD_REQUEST, "God cannot disable itself.")
            original = dict(user)
            requested_email = email if email is not None else username
            if requested_email is not None:
                normalized = self._normalize_email(requested_email)
                existing = self._find_user_by_username(normalized)
                if existing and existing.get("id") != user_id:
                    raise WebError(
                        HTTPStatus.CONFLICT, "Email already exists.", "email_exists"
                    )
                user["username"] = normalized
                user["email"] = normalized
            if display_name is not None:
                user["displayName"] = (display_name or "").strip() or user["username"]
            if role is not None:
                self._validate_role(role)
                user["role"] = role
            if account_ids is not None:
                requested_accounts = {str(a) for a in account_ids if a}
                if requested_accounts != set(user.get("accountIds") or []):
                    raise WebError(
                        HTTPStatus.BAD_REQUEST,
                        "LINE account ownership is managed by each user.",
                        "account_self_service_only",
                    )
            if active is not None:
                user["active"] = bool(active)
            if password:
                self._validate_password(password)
                salt = secrets.token_hex(16)
                user["salt"] = salt
                user["passwordHash"] = self._hash_password(password, salt)
            user["updatedAt"] = _now_iso()
            if not self._has_active_administrator(self.data.get("users", [])):
                user.clear()
                user.update(original)
                raise WebError(
                    HTTPStatus.BAD_REQUEST,
                    "At least one active God or Admin is required.",
                )
            if any(item.get("role") == "god" for item in self.data.get("users", [])) and not any(
                item.get("role") == "god" and item.get("active", True)
                for item in self.data.get("users", [])
            ):
                user.clear()
                user.update(original)
                raise WebError(HTTPStatus.BAD_REQUEST, "At least one active God is required.")
            self._save()
            return self._public_user_strict(user)

    def delete_user(
        self,
        user_id: str,
        current_user_id: str | None = None,
        actor: dict[str, Any] | None = None,
    ) -> None:
        with self.lock:
            if user_id == current_user_id:
                raise WebError(HTTPStatus.BAD_REQUEST, "You cannot delete your own user.")
            target = self._find_user(user_id)
            if target and target.get("role") == "god" and not self._is_god(actor):
                raise WebError(HTTPStatus.FORBIDDEN, "Only God can delete God users.")
            users = [user for user in self.data.get("users", []) if user.get("id") != user_id]
            if len(users) == len(self.data.get("users", [])):
                raise WebError(HTTPStatus.NOT_FOUND, "User not found.")
            if not self._has_active_administrator(users):
                raise WebError(
                    HTTPStatus.BAD_REQUEST,
                    "At least one active God or Admin is required.",
                )
            if any(item.get("role") == "god" for item in self.data.get("users", [])) and not any(
                item.get("role") == "god" and item.get("active", True) for item in users
            ):
                raise WebError(HTTPStatus.BAD_REQUEST, "At least one active God is required.")
            self.data["users"] = users
            self.data["sessions"] = {
                token: session
                for token, session in self.data.get("sessions", {}).items()
                if session.get("userId") != user_id
            }
            self._save()

    def change_password(
        self, user: dict[str, Any], current_password: str, new_password: str
    ) -> None:
        self._validate_password(new_password)
        with self.lock:
            stored = self._find_user(str(user.get("id") or ""))
            if stored is None:
                raise WebError(HTTPStatus.NOT_FOUND, "User not found.")
            expected = str(stored.get("passwordHash") or "")
            salt = str(stored.get("salt") or "")
            actual = self._hash_password(current_password or "", salt)
            if not hmac.compare_digest(expected, actual):
                raise WebError(HTTPStatus.UNAUTHORIZED, "Current password is incorrect.")
            new_salt = secrets.token_hex(16)
            stored["salt"] = new_salt
            stored["passwordHash"] = self._hash_password(new_password, new_salt)
            stored["updatedAt"] = _now_iso()
            self._save()

    def remove_account_access(self, account_id: str) -> None:
        with self.lock:
            changed = False
            for user in self.data.get("users", []):
                account_ids = list(user.get("accountIds") or [])
                if account_id in account_ids:
                    user["accountIds"] = [item for item in account_ids if item != account_id]
                    user["updatedAt"] = _now_iso()
                    changed = True
            if changed:
                self._save()

    @classmethod
    def cookie_header(cls, token: str, *, cookie_name: str | None = None) -> str:
        name = cookie_name or cls.cookie_name
        return f"{name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200"

    @classmethod
    def clear_cookie_header(cls, *, cookie_name: str | None = None) -> str:
        name = cookie_name or cls.cookie_name
        return f"{name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    @classmethod
    def _token_from_cookie(
        cls, cookie_header: str | None, *, cookie_name: str | None = None
    ) -> str:
        if not cookie_header:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return ""
        morsel = cookie.get(cookie_name or cls.cookie_name)
        return morsel.value if morsel else ""

    @classmethod
    def _hash_password(cls, password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            cls.iterations,
        )
        return digest.hex()

    @classmethod
    def roles_payload(cls) -> dict[str, Any]:
        return {
            role: {
                "label": data["label"],
                "permissions": list(data["permissions"]),
            }
            for role, data in ROLE_DEFINITIONS.items()
        }

    @classmethod
    def _public_user_strict(cls, user: dict[str, Any]) -> dict[str, Any]:
        result = cls.public_user(user)
        assert result is not None  # a concrete user always yields a payload
        return result

    @classmethod
    def public_user(cls, user: dict[str, Any] | None) -> dict[str, Any] | None:
        if user is None:
            return None
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "email": user.get("email") or cls._email_or_empty(
                str(user.get("username") or "")
            ),
            "displayName": user.get("displayName") or user.get("username"),
            "role": user.get("role"),
            "permissions": list(
                ROLE_DEFINITIONS.get(str(user.get("role")), {}).get("permissions", [])
            ),
            "accountIds": list(user.get("accountIds") or []),
            "active": bool(user.get("active", True)),
            "createdAt": user.get("createdAt"),
            "updatedAt": user.get("updatedAt"),
        }

    def _new_user(
        self,
        username: str,
        password: str,
        role: str,
        account_ids: list[str],
        display_name: str,
    ) -> dict[str, Any]:
        salt = secrets.token_hex(16)
        return {
            "id": uuid.uuid4().hex,
            "username": username,
            "email": self._email_or_empty(username),
            "displayName": display_name.strip() or username,
            "role": role,
            "salt": salt,
            "passwordHash": self._hash_password(password, salt),
            "accountIds": sorted({str(a) for a in account_ids if a}),
            "active": True,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }

    def _find_user(self, user_id: str) -> dict[str, Any] | None:
        for user in self.data.get("users", []):
            if isinstance(user, dict) and user.get("id") == user_id:
                return user
        return None

    def _ensure_accounts_unassigned(
        self, account_ids: list[str], *, exclude_user_id: str | None = None
    ) -> None:
        requested = {str(account_id) for account_id in account_ids if account_id}
        if not requested:
            return
        for user in self.data.get("users", []):
            if not isinstance(user, dict) or user.get("id") == exclude_user_id:
                continue
            if requested.intersection(user.get("accountIds") or []):
                raise WebError(
                    HTTPStatus.CONFLICT,
                    "A LINE account can belong to only one user.",
                    "account_already_owned",
                )

    def _find_user_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = (username or "").strip().lower()
        for user in self.data.get("users", []):
            if not isinstance(user, dict):
                continue
            if str(user.get("username") or "").lower() == normalized:
                return user
            if str(user.get("email") or "").lower() == normalized:
                return user
        return None

    @staticmethod
    def _is_active_user(user: dict[str, Any]) -> bool:
        return bool(
            user.get("active", True) and user.get("username") and user.get("passwordHash")
        )

    @staticmethod
    def _is_god(user: dict[str, Any] | None) -> bool:
        return bool(user and user.get("role") == "god" and user.get("active", True))

    @staticmethod
    def _has_active_administrator(users: list[dict[str, Any]]) -> bool:
        return any(
            user.get("role") in {"god", "admin"} and user.get("active", True)
            for user in users
        )

    @staticmethod
    def _normalize_username(username: str) -> str:
        username = (username or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_.@-]{3,64}", username):
            raise WebError(
                HTTPStatus.BAD_REQUEST,
                "Username must be 3-64 characters: letters, numbers, dot, dash, underscore, or @.",
            )
        return username

    @staticmethod
    def _normalize_email(email: str) -> str:
        email = (email or "").strip().lower()
        if len(email) > 254 or email.count("@") != 1:
            raise WebError(
                HTTPStatus.BAD_REQUEST, "Enter a valid email address.", "email_invalid"
            )
        local, domain = email.split("@", 1)
        local_ok = bool(
            local
            and len(local) <= 64
            and not local.startswith(".")
            and not local.endswith(".")
            and ".." not in local
            and re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
        )
        domain_ok = bool(
            re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
                domain,
            )
        )
        if not local_ok or not domain_ok:
            raise WebError(
                HTTPStatus.BAD_REQUEST, "Enter a valid email address.", "email_invalid"
            )
        return email

    @classmethod
    def _email_or_empty(cls, value: str) -> str:
        try:
            return cls._normalize_email(value)
        except WebError:
            return ""

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password or "") < 8:
            raise WebError(
                HTTPStatus.BAD_REQUEST,
                "Password must be at least 8 characters.",
                "password_too_short",
            )
        if len(password) > 128:
            raise WebError(
                HTTPStatus.BAD_REQUEST,
                "Password must not exceed 128 characters.",
                "password_too_long",
            )

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in ROLE_DEFINITIONS:
            raise WebError(HTTPStatus.BAD_REQUEST, "Unknown role.")


class AccountStore:
    def __init__(self, config: WebConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self.lock = threading.RLock()
        self.data = self._load()
        self._bootstrap_legacy()

    def _load(self) -> dict[str, Any]:
        data = self.store.get("accounts", {"accounts": []})
        if not isinstance(data, dict):
            data = {"accounts": []}
        accounts = data.get("accounts")
        if not isinstance(accounts, list):
            data["accounts"] = []
        return data

    def _bootstrap_legacy(self) -> None:
        with self.lock:
            accounts = self.data.setdefault("accounts", [])
            has_default = any(
                a.get("id") == "default" for a in accounts if isinstance(a, dict)
            )
            if os.path.exists(self.config.tokens_file) and not has_default:
                accounts.append(
                    {
                        "id": "default",
                        "label": "Default",
                        "tokenFile": self.config.tokens_file,
                        "mid": None,
                        "userid": None,
                        "createdAt": _now_iso(),
                        "updatedAt": _now_iso(),
                    }
                )
            if accounts and not self.data.get("activeAccountId"):
                self.data["activeAccountId"] = accounts[0].get("id")
            self.save()

    def save(self) -> None:
        self.store.set("accounts", self.data)

    def list_accounts(self, allowed_ids: set[str] | None = None) -> list[dict[str, Any]]:
        with self.lock:
            out = []
            for account in self.data.get("accounts", []):
                if not isinstance(account, dict):
                    continue
                if allowed_ids is not None and account.get("id") not in allowed_ids:
                    continue
                token_file = account.get("tokenFile") or ""
                item = dict(account)
                item["tokenFileExists"] = bool(token_file and os.path.exists(token_file))
                out.append(item)
            return out

    def active_id(self) -> str | None:
        with self.lock:
            active = self.data.get("activeAccountId")
            if active and self.get(active):
                return str(active)
            accounts = self.list_accounts()
            return accounts[0]["id"] if accounts else None

    def get(self, account_id: str | None) -> dict[str, Any] | None:
        if not account_id:
            return None
        with self.lock:
            for account in self.data.get("accounts", []):
                if isinstance(account, dict) and account.get("id") == account_id:
                    return dict(account)
        return None

    def set_active(self, account_id: str) -> dict[str, Any]:
        with self.lock:
            account = self.get(account_id)
            if account is None:
                raise WebError(HTTPStatus.NOT_FOUND, "Account not found.")
            self.data["activeAccountId"] = account_id
            self.save()
            return account

    def update_profile(self, account_id: str, profile: dict[str, Any]) -> None:
        with self.lock:
            for account in self.data.get("accounts", []):
                if isinstance(account, dict) and account.get("id") == account_id:
                    account["label"] = profile.get("displayName") or account.get("label")
                    account["mid"] = profile.get("mid") or account.get("mid")
                    account["userid"] = profile.get("userid") or account.get("userid")
                    account["updatedAt"] = _now_iso()
                    self.save()
                    return

    def update_label(self, account_id: str, label: str) -> dict[str, Any]:
        label = (label or "").strip()
        if not label:
            raise WebError(HTTPStatus.BAD_REQUEST, "Account name is required.")
        with self.lock:
            for account in self.data.get("accounts", []):
                if isinstance(account, dict) and account.get("id") == account_id:
                    account["label"] = label
                    account["updatedAt"] = _now_iso()
                    self.save()
                    return dict(account)
        raise WebError(HTTPStatus.NOT_FOUND, "Account not found.")

    def add_from_api(
        self,
        api: OkLine,
        profile: dict[str, Any],
        requested_label: str | None,
        owner_id: str,
    ) -> dict[str, Any]:
        label = (requested_label or profile.get("displayName") or "Line Account").strip()
        mid = str(profile.get("mid") or uuid.uuid4().hex)
        base = _safe_slug(label) + "-" + _safe_slug(mid[-8:], "line")
        with self.lock:
            existing = {
                a.get("id") for a in self.data.get("accounts", []) if isinstance(a, dict)
            }
            account_id = base
            suffix = 2
            while account_id in existing:
                account_id = f"{base}-{suffix}"
                suffix += 1
            os.makedirs(self.config.accounts_dir, exist_ok=True)
            token_file = str(Path(self.config.accounts_dir) / f"{account_id}.tokens.json")
            api.save_tokens(token_file)
            account = {
                "id": account_id,
                "label": label,
                "tokenFile": token_file,
                "mid": profile.get("mid"),
                "userid": profile.get("userid"),
                "ownerId": owner_id,
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
            }
            self.data.setdefault("accounts", []).append(account)
            self.data["activeAccountId"] = account_id
            self.save()
            return dict(account)

    def remove(self, account_id: str, *, delete_file: bool = True) -> dict[str, Any]:
        with self.lock:
            accounts = self.data.get("accounts", [])
            found = None
            kept = []
            for account in accounts:
                if isinstance(account, dict) and account.get("id") == account_id:
                    found = account
                else:
                    kept.append(account)
            if found is None:
                raise WebError(HTTPStatus.NOT_FOUND, "Account not found.")
            self.data["accounts"] = kept
            if self.data.get("activeAccountId") == account_id:
                self.data["activeAccountId"] = kept[0].get("id") if kept else None
            self.save()
        token_file = found.get("tokenFile")
        if delete_file and token_file and os.path.exists(token_file):
            os.remove(token_file)
        return dict(found)


class ScheduleEngine:
    def __init__(self, state: WebState) -> None:
        self.state = state
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="okline-web-scheduler", daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self.stop_event.wait(10.0):
            try:
                self.state.run_due_schedules()
            except Exception:
                pass


class WebState:
    def __init__(self, config: WebConfig) -> None:
        self.config = config
        self.lock = threading.RLock()
        self.api_lock = threading.RLock()
        self.store = _make_state_store(config)
        self.web_auth = WebAuth(self.store)
        self.account_store = AccountStore(config, self.store)
        self._migrate_tenant_data()
        self.apis: dict[str, OkLine] = {}
        self.login: dict[str, Any] = {"state": "idle"}
        self.schedule_lock = threading.RLock()
        self.schedules: list[dict[str, Any]] = self._load_schedules()
        self.scheduler = ScheduleEngine(self)

    def _migrate_tenant_data(self) -> None:
        users = [user for user in self.web_auth.data.get("users", []) if isinstance(user, dict)]
        legacy_owner = next(
            (
                user
                for user in users
                if user.get("role") == "admin" and user.get("active", True)
            ),
            next((user for user in users if user.get("role") != "god"), None),
        )
        if legacy_owner is None:
            return
        owner_id = str(legacy_owner.get("id") or "")
        if not owner_id:
            return

        changed_auth = False
        assigned = {
            str(account_id)
            for user in users
            for account_id in (user.get("accountIds") or [])
            if account_id
        }
        accounts_changed = False
        for account in self.account_store.data.get("accounts", []):
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id") or "")
            if account_id and account_id not in assigned:
                account_ids = set(legacy_owner.get("accountIds") or [])
                account_ids.add(account_id)
                legacy_owner["accountIds"] = sorted(account_ids)
                assigned.add(account_id)
                changed_auth = True
            owner = self.web_auth.user_for_account(account_id)
            expected_owner = str((owner or legacy_owner).get("id") or "")
            if expected_owner and account.get("ownerId") != expected_owner:
                account["ownerId"] = expected_owner
                accounts_changed = True
        if changed_auth:
            legacy_owner["updatedAt"] = _now_iso()
            self.web_auth._save()
        if accounts_changed:
            self.account_store.save()

        pattern_data = self.store.get("patterns", {"patterns": []})
        patterns = pattern_data.get("patterns", []) if isinstance(pattern_data, dict) else []
        patterns_changed = False
        for pattern in patterns if isinstance(patterns, list) else []:
            if not isinstance(pattern, dict):
                continue
            if not pattern.get("ownerId"):
                pattern["ownerId"] = owner_id
                patterns_changed = True
            if not pattern.get("categoryId"):
                pattern["categoryId"] = DEFAULT_PATTERN_CATEGORY_ID
                patterns_changed = True
        if patterns_changed:
            self.store.set(
                "patterns",
                {
                    "patterns": patterns,
                    "categories": (
                        pattern_data.get("categories", [])
                        if isinstance(pattern_data, dict)
                        else []
                    ),
                },
            )

        ai_data = self.store.get("ai_settings", {})
        if isinstance(ai_data, dict) and "tenants" not in ai_data:
            tenants = {owner_id: ai_data} if ai_data else {}
            self.store.set("ai_settings", {"tenants": tenants})

        schedule_data = self.store.get("schedules", {"schedules": []})
        schedules = (
            schedule_data.get("schedules", []) if isinstance(schedule_data, dict) else []
        )
        schedules_changed = False
        for schedule in schedules if isinstance(schedules, list) else []:
            if not isinstance(schedule, dict) or schedule.get("ownerId"):
                continue
            owner = self.web_auth.user_for_account(str(schedule.get("accountId") or ""))
            schedule["ownerId"] = str((owner or legacy_owner).get("id") or owner_id)
            schedules_changed = True
        if schedules_changed:
            self.store.set("schedules", {"schedules": schedules})

    def close(self) -> None:
        self.scheduler.close()
        with self.lock:
            apis = list(self.apis.values())
            self.apis = {}
        for api in apis:
            api.close()

    def _new_api(self, account_id: str | None = None) -> OkLine:
        redact = not self.config.show_secrets
        if self.config.access_token and account_id in (None, "__manual__"):
            return OkLine(
                access_token=self.config.access_token,
                refresh_token=self.config.refresh_token,
                redact=redact,
            )
        account = self.account_store.get(account_id) if account_id else None
        token_file = account.get("tokenFile") if account else None
        if token_file and os.path.exists(token_file):
            return OkLine.from_tokens_file(token_file, redact=redact)
        return OkLine(redact=redact)

    def get_api(self, account_id: str | None = None, *, require_auth: bool = True) -> OkLine:
        if self.config.access_token and account_id is None:
            cache_key = "__manual__"
        else:
            account_id = account_id or self.account_store.active_id()
            cache_key = account_id or "__anonymous__"
        with self.lock:
            if cache_key not in self.apis:
                self.apis[cache_key] = self._new_api(account_id)
            api = self.apis[cache_key]
        if require_auth and not api.tokens.access_token:
            raise WebError(
                HTTPStatus.UNAUTHORIZED, "No session. Start QR login first.", "no_session"
            )
        return api

    def replace_api(self, account_id: str, api: OkLine) -> None:
        with self.lock:
            old = self.apis.get(account_id)
            self.apis[account_id] = api
        if old is not None and old is not api:
            old.close()

    def close_api(self, account_id: str) -> None:
        with self.lock:
            api = self.apis.pop(account_id, None)
        if api is not None:
            api.close()

    def status(
        self, account_id: str | None = None, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        account_id = (account_id or "").strip()
        allowed_ids = self.web_auth.allowed_account_ids(user)
        api: OkLine | None = None
        authenticated = False
        profile: dict[str, Any] | None = None
        profile_error: str | None = None
        e2ee_ready = False
        if account_id:
            if not self.account_store.get(account_id):
                raise WebError(HTTPStatus.NOT_FOUND, "Account not found.")
            if not self.web_auth.can_access_account(user, account_id):
                raise WebError(
                    HTTPStatus.FORBIDDEN, "Permission denied for this LINE account."
                )
            api = self.get_api(account_id, require_auth=False)
            authenticated = bool(api.tokens.access_token)
        if account_id and api and authenticated:
            try:
                with self.api_lock:
                    profile = api.get_profile()
                if isinstance(profile, dict):
                    self.account_store.update_profile(account_id, profile)
            except Exception as exc:
                profile_error = str(exc)
            e2ee_ready = bool(getattr(api, "e2ee", None) and api.e2ee.is_ready())
        return {
            "version": __version__,
            "authenticated": authenticated,
            "activeAccountId": account_id,
            "accounts": self.account_store.list_accounts(allowed_ids),
            "tokenFile": self.config.tokens_file,
            "tokenFileExists": os.path.exists(self.config.tokens_file),
            "storage": self.store.label(),
            "nodeAvailable": LtsmBridge.is_available(),
            "e2eeReady": e2ee_ready,
            "profile": profile,
            "profileError": profile_error,
        }

    def accounts(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        allowed_ids = self.web_auth.allowed_account_ids(user)
        accounts = self.account_store.list_accounts(allowed_ids)
        active_id = self.account_store.active_id()
        visible_ids = {str(account.get("id") or "") for account in accounts}
        if active_id not in visible_ids:
            active_id = accounts[0]["id"] if accounts else None
        return {
            "activeAccountId": active_id,
            "accounts": accounts,
        }

    def switch_account(
        self, account_id: str, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.web_auth.can_access_account(user, account_id):
            raise WebError(HTTPStatus.FORBIDDEN, "Permission denied for this LINE account.")
        account = self.account_store.set_active(account_id)
        return {"ok": True, "account": account, "status": self.status(account_id, user)}

    def start_login(
        self,
        wait_seconds: float = 180.0,
        account_name: str | None = None,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if self.login.get("state") in {"starting", "qr", "pin"}:
                raise WebError(
                    HTTPStatus.CONFLICT, "QR login is already running.", "login_running"
                )
            login_id = uuid.uuid4().hex
            self.login = {
                "state": "starting",
                "id": login_id,
                "startedAt": time.time(),
                "qrUrl": None,
                "qrSvg": None,
                "pin": None,
                "error": None,
                "profile": None,
                "account": None,
                "accountName": account_name,
                "ownerId": str((user or {}).get("id") or ""),
            }
        thread = threading.Thread(
            target=self._login_worker,
            args=(
                float(wait_seconds),
                account_name,
                login_id,
                str((user or {}).get("id") or ""),
            ),
            daemon=True,
        )
        thread.start()
        return self.login_state(user)

    def login_state(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            owner_id = str(self.login.get("ownerId") or "")
            user_id = str((user or {}).get("id") or "")
            if owner_id and owner_id != user_id and not self.web_auth._is_god(user):
                return {"state": "idle"}
            return dict(self.login)

    def cancel_login(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        """Free the QR-login worker slot so a fresh login can start.

        The in-flight worker (if any) keeps its ``id``; because we install a
        new idle login state here, that stale worker's callbacks/result become
        no-ops (they only write when their ``id`` still matches).
        """
        with self.lock:
            owner_id = str(self.login.get("ownerId") or "")
            user_id = str((user or {}).get("id") or "")
            if owner_id and owner_id != user_id and not self.web_auth._is_god(user):
                raise WebError(HTTPStatus.FORBIDDEN, "Login belongs to another user.")
            self.login = {"state": "idle", "id": uuid.uuid4().hex}
        return {"ok": True, "state": "idle"}

    def _login_worker(
        self,
        wait_seconds: float,
        account_name: str | None,
        login_id: str,
        owner_id: str,
    ) -> None:
        client = OkLine(record=False, redact=not self.config.show_secrets)
        keep_client = False

        def _current() -> bool:
            return self.login.get("id") == login_id

        def on_qr(url: str) -> None:
            with self.lock:
                if _current():
                    self.login.update({"state": "qr", "qrUrl": url, "qrSvg": _qr_svg(url)})

        def on_pin(pin: str) -> None:
            with self.lock:
                if _current():
                    self.login.update({"state": "pin", "pin": pin})

        try:
            result = client.qr_login(on_qr=on_qr, on_pin=on_pin, wait_seconds=wait_seconds)
            if not result.access_token:
                raise WebError(
                    HTTPStatus.BAD_GATEWAY, result.display_message or "Login failed"
                )
            profile = client.get_profile()
            with self.lock:
                cancelled = not _current()
            if cancelled:
                # The user cancelled while we were finishing; drop the session
                # instead of registering an account they no longer expect.
                return
            account = self.account_store.add_from_api(
                client, profile, account_name, owner_id
            )
            self.web_auth.grant_account_access(owner_id, account["id"])
            self.replace_api(account["id"], client)
            keep_client = True
            with self.lock:
                if _current():
                    self.login.update(
                        {
                            "state": "success",
                            "profile": profile,
                            "account": account,
                            "error": None,
                        }
                    )
        except Exception as exc:
            with self.lock:
                if _current():
                    self.login.update({"state": "error", "error": str(exc)})
        finally:
            if not keep_client:
                client.close()

    def logout_account(
        self, account_id: str, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        account_id = _required_account_id(account_id)
        api = self.get_api(account_id, require_auth=False)
        server_error = None
        if api.tokens.access_token:
            try:
                with self.api_lock:
                    api.auth.logout()
            except Exception as exc:
                server_error = str(exc)
        self.close_api(account_id)
        self.account_store.remove(account_id, delete_file=True)
        self.web_auth.remove_account_access(account_id)
        self.disable_schedules_for_account(account_id)
        return {
            "ok": True,
            "serverError": server_error,
            "accounts": {
                "accounts": self.account_store.list_accounts(
                    self.web_auth.allowed_account_ids(user)
                )
            },
        }

    def delete_account(
        self, account_id: str, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.close_api(account_id)
        removed = self.account_store.remove(account_id, delete_file=True)
        self.web_auth.remove_account_access(account_id)
        self.disable_schedules_for_account(account_id)
        return {
            "ok": True,
            "removed": removed,
            "accounts": {
                "accounts": self.account_store.list_accounts(
                    self.web_auth.allowed_account_ids(user)
                )
            },
        }

    def update_account(
        self,
        account_id: str,
        label: str,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        account = self.account_store.update_label(_required_account_id(account_id), label)
        return {
            "ok": True,
            "account": account,
            "accounts": {
                "accounts": self.account_store.list_accounts(
                    self.web_auth.allowed_account_ids(user)
                )
            },
        }

    def users_payload(self) -> dict[str, Any]:
        return {
            "users": self.web_auth.list_users(),
            "roles": self.web_auth.roles_payload(),
            "accounts": self.account_store.list_accounts(),
        }

    def god_users_payload(self) -> dict[str, Any]:
        payload = self.users_payload()
        payload["users"] = [
            user for user in payload["users"] if user.get("role") != "god"
        ]
        payload["roles"] = {
            role: meta for role, meta in payload["roles"].items() if role != "god"
        }
        payload.pop("accounts", None)
        return payload

    def user_detail(
        self, user_id: str, actor: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not self.web_auth._is_god(actor):
            raise WebError(HTTPStatus.FORBIDDEN, "Only God can view tenant details.")
        target = self.web_auth._find_user(user_id)
        if target is None:
            raise WebError(HTTPStatus.NOT_FOUND, "User not found.")
        public_user = self.web_auth._public_user_strict(target)
        account_ids = set(target.get("accountIds") or [])
        accounts = []
        for account in self.account_store.list_accounts(account_ids):
            accounts.append(
                {
                    "id": account.get("id"),
                    "label": account.get("label"),
                    "mid": account.get("mid"),
                    "userid": account.get("userid"),
                    "tokenFileExists": account.get("tokenFileExists"),
                    "createdAt": account.get("createdAt"),
                }
            )
        patterns = [
            {
                "id": pattern.get("id"),
                "name": pattern.get("name"),
                "text": pattern.get("text"),
                "categoryId": pattern.get("categoryId"),
                "categoryName": pattern.get("categoryName"),
            }
            for pattern in self.list_patterns(target)["patterns"]
        ]
        schedules = [
            {
                "id": schedule.get("id"),
                "name": schedule.get("name"),
                "accountId": schedule.get("accountId"),
                "contentSource": schedule.get("contentSource"),
                "enabled": bool(schedule.get("enabled")),
                "status": schedule.get("status"),
                "sentCount": int(schedule.get("sentCount") or 0),
            }
            for schedule in self.schedules
            if schedule.get("accountId") in account_ids
        ]
        ai = self.ai_settings(target)
        provider = str(ai.get("provider") or "google")
        provider_data = (ai.get("providers") or {}).get(provider) or {}
        return {
            "user": public_user,
            "accounts": accounts,
            "patterns": patterns,
            "schedules": schedules,
            "ai": {
                "provider": provider,
                "providerLabel": provider_data.get("label"),
                "model": provider_data.get("model"),
                "modelLabel": (provider_data.get("modelLabels") or {}).get(
                    provider_data.get("model")
                ),
                "aspectRatio": provider_data.get("aspectRatio"),
                "configured": bool(provider_data.get("hasApiKey")),
            },
        }

    # -- bot action log -----------------------------------------------------
    def _bot_log_entry(
        self,
        action: str,
        *,
        account_id: str | None = None,
        schedule: dict[str, Any] | None = None,
        ok: bool = True,
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schedule = schedule if isinstance(schedule, dict) else {}
        entry = {
            "id": uuid.uuid4().hex,
            "at": _now_iso(),
            "ts": time.time(),
            "action": str(action or "event"),
            "ok": bool(ok),
            "accountId": account_id or schedule.get("accountId"),
            "scheduleId": schedule.get("id"),
            "scheduleName": schedule.get("name"),
            "target": schedule.get("to"),
            "contentSource": schedule.get("contentSource"),
            "mode": schedule.get("mode"),
        }
        if detail:
            entry["detail"] = _bot_log_detail(entry["action"], detail)
        if data:
            entry["data"] = data
        return {key: value for key, value in entry.items() if value not in (None, "")}

    def append_bot_log(
        self,
        action: str,
        *,
        account_id: str | None = None,
        schedule: dict[str, Any] | None = None,
        ok: bool = True,
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        try:
            with self.schedule_lock:
                raw = self.store.get("bot_logs", {"logs": []})
                logs = raw.get("logs", []) if isinstance(raw, dict) else []
                if not isinstance(logs, list):
                    logs = []
                logs.append(
                    self._bot_log_entry(
                        action,
                        account_id=account_id,
                        schedule=schedule,
                        ok=ok,
                        detail=detail,
                        data=data,
                    )
                )
                del logs[:-500]
                self.store.set("bot_logs", {"logs": logs})
        except Exception:
            # Logging should never prevent the bot action itself from completing.
            pass

    def list_bot_logs(
        self,
        account_id: str | None = None,
        user: dict[str, Any] | None = None,
        *,
        limit: int = 200,
    ) -> dict[str, Any]:
        account_id = (account_id or "").strip()
        if account_id and not self.web_auth.can_access_account(user, account_id):
            raise WebError(HTTPStatus.FORBIDDEN, "Permission denied for this LINE account.")
        allowed_ids = self.web_auth.allowed_account_ids(user)
        raw = self.store.get("bot_logs", {"logs": []})
        logs = raw.get("logs", []) if isinstance(raw, dict) else []
        if not isinstance(logs, list):
            logs = []
        visible = []
        for log in logs:
            if not isinstance(log, dict):
                continue
            item = dict(log)
            if item.get("detail"):
                item["detail"] = _bot_log_detail(item.get("action"), item.get("detail"))
            visible.append(item)
        if account_id:
            visible = [log for log in visible if log.get("accountId") == account_id]
        elif allowed_ids is not None:
            visible = [log for log in visible if log.get("accountId") in allowed_ids]
        visible = visible[-max(1, min(500, int(limit))) :]
        visible.reverse()
        return {"logs": visible, "count": len(visible)}

    def clear_bot_logs(
        self, account_id: str, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        account_id = _required_account_id(account_id)
        if not self.web_auth.can_access_account(user, account_id):
            raise WebError(HTTPStatus.FORBIDDEN, "Permission denied for this LINE account.")
        with self.schedule_lock:
            raw = self.store.get("bot_logs", {"logs": []})
            logs = raw.get("logs", []) if isinstance(raw, dict) else []
            if not isinstance(logs, list):
                logs = []
            kept = [
                log
                for log in logs
                if not isinstance(log, dict) or log.get("accountId") != account_id
            ]
            marker = self._bot_log_entry(
                "logs.clear",
                account_id=account_id,
                detail="Cleared bot logs for this account.",
            )
            kept.append(marker)
            del kept[:-500]
            self.store.set("bot_logs", {"logs": kept})
        return {"ok": True, "logs": [marker], "count": 1}

    def create_user(
        self, body: dict[str, Any], actor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        user = self.web_auth.create_user(
            str(body.get("email") or body.get("username") or ""),
            str(body.get("password") or ""),
            str(body.get("role") or "viewer"),
            _string_list(body.get("accountIds")),
            str(body.get("displayName") or ""),
            actor=actor,
        )
        return {"ok": True, "user": user, **self.users_payload()}

    def update_user(
        self, body: dict[str, Any], actor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        user_id = str(body.get("id") or "")
        user = self.web_auth.update_user(
            user_id,
            username=str(body["username"]) if "username" in body else None,
            email=str(body["email"]) if "email" in body else None,
            display_name=str(body["displayName"]) if "displayName" in body else None,
            role=str(body["role"]) if "role" in body else None,
            account_ids=_string_list(body.get("accountIds")) if "accountIds" in body else None,
            active=bool(body.get("active")) if "active" in body else None,
            password=str(body.get("password") or "") or None,
            actor=actor,
        )
        return {"ok": True, "user": user, **self.users_payload()}

    def delete_user(
        self, body: dict[str, Any], current_user: dict[str, Any]
    ) -> dict[str, Any]:
        user_id = str(body.get("id") or "")
        target = self.web_auth._find_user(user_id)
        account_ids = set((target or {}).get("accountIds") or [])
        self.web_auth.delete_user(
            user_id,
            current_user_id=str(current_user.get("id") or ""),
            actor=current_user,
        )
        for account_id in account_ids:
            self.close_api(account_id)
            self.account_store.remove(account_id, delete_file=True)
        with self.schedule_lock:
            self.schedules = [
                schedule
                for schedule in self.schedules
                if schedule.get("accountId") not in account_ids
            ]
            self.save_schedules()
        pattern_data = self.store.get("patterns", {"patterns": []})
        patterns = pattern_data.get("patterns", []) if isinstance(pattern_data, dict) else []
        if isinstance(patterns, list):
            categories = (
                pattern_data.get("categories", [])
                if isinstance(pattern_data, dict)
                else []
            )
            self.store.set(
                "patterns",
                {
                    "patterns": [
                        pattern
                        for pattern in patterns
                        if not isinstance(pattern, dict)
                        or str(pattern.get("ownerId") or "") != user_id
                    ],
                    "categories": [
                        category
                        for category in categories
                        if not isinstance(category, dict)
                        or str(category.get("ownerId") or "") != user_id
                    ],
                },
            )
        ai_root = self.store.get("ai_settings", {"tenants": {}})
        if isinstance(ai_root, dict) and isinstance(ai_root.get("tenants"), dict):
            ai_root["tenants"].pop(user_id, None)
            self.store.set("ai_settings", ai_root)
        log_data = self.store.get("bot_logs", {"logs": []})
        logs = log_data.get("logs", []) if isinstance(log_data, dict) else []
        if isinstance(logs, list):
            self.store.set(
                "bot_logs",
                {
                    "logs": [
                        log
                        for log in logs
                        if not isinstance(log, dict)
                        or log.get("accountId") not in account_ids
                    ]
                },
            )
        return {"ok": True, **self.users_payload()}

    # -- schedules ---------------------------------------------------------
    def _load_schedules(self) -> list[dict[str, Any]]:
        data = self.store.get("schedules", {"schedules": []})
        schedules = data.get("schedules", []) if isinstance(data, dict) else []
        if not isinstance(schedules, list):
            return []
        return [s for s in schedules if isinstance(s, dict)]

    def save_schedules(self) -> None:
        with self.schedule_lock:
            self.store.set("schedules", {"schedules": self.schedules})

    def list_schedules(
        self, account_id: str | None = None, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        account_id = (account_id or "").strip()
        allowed_ids = self.web_auth.allowed_account_ids(user)
        with self.schedule_lock:
            schedules = [dict(job) for job in self.schedules]
            if account_id:
                if not self.web_auth.can_access_account(user, account_id):
                    raise WebError(
                        HTTPStatus.FORBIDDEN, "Permission denied for this LINE account."
                    )
                schedules = [job for job in schedules if job.get("accountId") == account_id]
            elif allowed_ids is not None:
                schedules = [job for job in schedules if job.get("accountId") in allowed_ids]
            return {
                "schedules": schedules,
                "accounts": self.account_store.list_accounts(allowed_ids),
            }

    def create_schedule(self, body: dict[str, Any]) -> dict[str, Any]:
        account_id = _required_account_id(str(body.get("accountId") or ""))
        if not self.account_store.get(account_id):
            raise WebError(HTTPStatus.BAD_REQUEST, "A valid account is required.")
        job = _schedule_from_body(body, account_id=account_id)
        with self.schedule_lock:
            self.schedules.append(job)
            self.save_schedules()
            self.append_bot_log(
                "schedule.create",
                account_id=account_id,
                schedule=job,
                detail=f"{job.get('contentSource')} -> {job.get('to')}",
            )
        return {"ok": True, "schedule": job}

    def update_schedule(self, schedule_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.schedule_lock:
            job = self._find_schedule(schedule_id)
            account_id = _required_account_id(str(body.get("accountId") or ""))
            _ensure_schedule_account(job, account_id)
            if not self.account_store.get(account_id):
                raise WebError(HTTPStatus.BAD_REQUEST, "A valid account is required.")
            updated = _schedule_from_body(body, account_id=account_id, existing=job)
            job.clear()
            job.update(updated)
            self.save_schedules()
            self.append_bot_log(
                "schedule.update",
                account_id=account_id,
                schedule=job,
                detail=f"{job.get('contentSource')} -> {job.get('to')}",
            )
            return {"ok": True, "schedule": dict(job)}

    def toggle_schedule(
        self, schedule_id: str, enabled: bool, account_id: str | None = None
    ) -> dict[str, Any]:
        with self.schedule_lock:
            job = self._find_schedule(schedule_id)
            _ensure_schedule_account(job, account_id)
            job["enabled"] = bool(enabled)
            job["status"] = "waiting" if enabled else "paused"
            if enabled:
                job["nextRunAt"] = _compute_next_epoch(job)
            self.save_schedules()
            self.append_bot_log(
                "schedule.resume" if enabled else "schedule.pause",
                account_id=str(job.get("accountId") or ""),
                schedule=job,
            )
            return {"ok": True, "schedule": dict(job)}

    def delete_schedule(
        self, schedule_id: str, account_id: str | None = None
    ) -> dict[str, Any]:
        with self.schedule_lock:
            job = self._find_schedule(schedule_id)
            _ensure_schedule_account(job, account_id)
            snapshot = dict(job)
            before = len(self.schedules)
            self.schedules = [job for job in self.schedules if job.get("id") != schedule_id]
            if len(self.schedules) == before:
                raise WebError(HTTPStatus.NOT_FOUND, "Schedule not found.")
            self.save_schedules()
            self.append_bot_log(
                "schedule.delete",
                account_id=str(snapshot.get("accountId") or ""),
                schedule=snapshot,
            )
        return {"ok": True}

    def clear_stuck_schedules(
        self, account_id: str, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        account_id = _required_account_id(account_id)
        if not self.web_auth.can_access_account(user, account_id):
            raise WebError(HTTPStatus.FORBIDDEN, "Permission denied for this LINE account.")
        cleared = 0
        with self.schedule_lock:
            for job in self.schedules:
                if job.get("accountId") != account_id:
                    continue
                status = str(job.get("status") or "").lower()
                stuck = bool(
                    job.get("running")
                    or status in {"running", "error"}
                    or job.get("lastError")
                )
                if not stuck:
                    continue
                old_status = str(job.get("status") or "")
                was_running = bool(job.get("running"))
                job["running"] = False
                job["lastError"] = None
                if job.get("enabled"):
                    if not job.get("nextRunAt"):
                        job["nextRunAt"] = _compute_next_epoch(job)
                    job["status"] = "waiting" if job.get("nextRunAt") else "paused"
                else:
                    job["status"] = "paused"
                job["updatedAt"] = _now_iso()
                _append_history(
                    job,
                    {
                        "at": _now_iso(),
                        "ok": True,
                        "action": "clear_stuck",
                        "oldStatus": old_status,
                        "wasRunning": was_running,
                    },
                )
                self.append_bot_log(
                    "schedule.clear_stuck",
                    account_id=account_id,
                    schedule=job,
                    detail=f"reset from {old_status or 'unknown'}",
                    data={"oldStatus": old_status, "wasRunning": was_running},
                )
                cleared += 1
            if cleared:
                self.save_schedules()
            schedules = [
                dict(job) for job in self.schedules if job.get("accountId") == account_id
            ]
        return {"ok": True, "cleared": cleared, "schedules": schedules}

    # -- message patterns (tenant-owned) ---------------------------------
    def list_patterns(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self.store.get("patterns", {"patterns": []})
        items = data.get("patterns", []) if isinstance(data, dict) else []
        raw_categories = data.get("categories", []) if isinstance(data, dict) else []
        owner_id = str((user or {}).get("id") or "")
        categories = [
            {
                "id": DEFAULT_PATTERN_CATEGORY_ID,
                "name": "General",
                "system": True,
            }
        ]
        categories.extend(
            dict(category)
            for category in raw_categories
            if isinstance(category, dict)
            and str(category.get("ownerId") or "") == owner_id
        )
        category_names = {
            str(category.get("id") or ""): str(category.get("name") or "")
            for category in categories
        }
        patterns = []
        for pattern in items:
            if not isinstance(pattern, dict) or str(pattern.get("ownerId") or "") != owner_id:
                continue
            item = dict(pattern)
            category_id = str(item.get("categoryId") or DEFAULT_PATTERN_CATEGORY_ID)
            if category_id not in category_names:
                category_id = DEFAULT_PATTERN_CATEGORY_ID
            item["categoryId"] = category_id
            item["categoryName"] = category_names[category_id]
            patterns.append(item)
        patterns.sort(
            key=lambda item: (
                str(item.get("categoryName") or "").casefold(),
                str(item.get("name") or "").casefold(),
            )
        )
        return {
            "patterns": patterns,
            "categories": categories,
        }

    def create_pattern_category(
        self, body: dict[str, Any], user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise WebError(HTTPStatus.BAD_REQUEST, "Category name is required.")
        if len(name) > 80:
            raise WebError(HTTPStatus.BAD_REQUEST, "Category name is too long.")
        owner_id = str((user or {}).get("id") or "")
        with self.schedule_lock:
            data = self.store.get("patterns", {"patterns": [], "categories": []})
            items = data.get("patterns", []) if isinstance(data, dict) else []
            categories = data.get("categories", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            if not isinstance(categories, list):
                categories = []
            duplicate = any(
                isinstance(category, dict)
                and str(category.get("ownerId") or "") == owner_id
                and str(category.get("name") or "").casefold() == name.casefold()
                for category in categories
            )
            if duplicate or name.casefold() in {"general", "ทั่วไป"}:
                raise WebError(HTTPStatus.CONFLICT, "Category name already exists.")
            category = {
                "id": uuid.uuid4().hex,
                "ownerId": owner_id,
                "name": name,
                "createdAt": _now_iso(),
            }
            categories.append(category)
            self.store.set(
                "patterns", {"patterns": items, "categories": categories}
            )
            self.append_bot_log(
                "pattern.category.create",
                account_id=str(body.get("accountId") or ""),
                detail=name,
            )
        return {"ok": True, "category": category}

    def update_pattern_category(
        self, body: dict[str, Any], user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        category_id = str(body.get("id") or "").strip()
        name = str(body.get("name") or "").strip()
        if not category_id or category_id == DEFAULT_PATTERN_CATEGORY_ID:
            raise WebError(HTTPStatus.BAD_REQUEST, "The General category cannot be edited.")
        if not name:
            raise WebError(HTTPStatus.BAD_REQUEST, "Category name is required.")
        if len(name) > 80:
            raise WebError(HTTPStatus.BAD_REQUEST, "Category name is too long.")
        if name.casefold() in {"general", "ทั่วไป"}:
            raise WebError(HTTPStatus.CONFLICT, "Category name already exists.")
        owner_id = str((user or {}).get("id") or "")
        with self.schedule_lock:
            data = self.store.get("patterns", {"patterns": [], "categories": []})
            items = data.get("patterns", []) if isinstance(data, dict) else []
            categories = data.get("categories", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            if not isinstance(categories, list):
                categories = []
            category = next(
                (
                    item
                    for item in categories
                    if isinstance(item, dict)
                    and str(item.get("id") or "") == category_id
                    and str(item.get("ownerId") or "") == owner_id
                ),
                None,
            )
            if category is None:
                raise WebError(HTTPStatus.NOT_FOUND, "Category not found.")
            duplicate = any(
                isinstance(item, dict)
                and item is not category
                and str(item.get("ownerId") or "") == owner_id
                and str(item.get("name") or "").casefold() == name.casefold()
                for item in categories
            )
            if duplicate:
                raise WebError(HTTPStatus.CONFLICT, "Category name already exists.")
            old_name = str(category.get("name") or "")
            category["name"] = name
            category["updatedAt"] = _now_iso()
            self.store.set(
                "patterns", {"patterns": items, "categories": categories}
            )
            self.append_bot_log(
                "pattern.category.update",
                account_id=str(body.get("accountId") or ""),
                detail=f"{old_name} -> {name}",
            )
        return {"ok": True, "category": dict(category)}

    def delete_pattern_category(
        self,
        category_id: str,
        account_id: str | None = None,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        category_id = str(category_id or "").strip()
        if not category_id or category_id == DEFAULT_PATTERN_CATEGORY_ID:
            raise WebError(HTTPStatus.BAD_REQUEST, "The General category cannot be deleted.")
        owner_id = str((user or {}).get("id") or "")
        with self.schedule_lock:
            data = self.store.get("patterns", {"patterns": [], "categories": []})
            items = data.get("patterns", []) if isinstance(data, dict) else []
            categories = data.get("categories", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            if not isinstance(categories, list):
                categories = []
            removed = next(
                (
                    category
                    for category in categories
                    if isinstance(category, dict)
                    and str(category.get("id") or "") == category_id
                    and str(category.get("ownerId") or "") == owner_id
                ),
                None,
            )
            if removed is None:
                raise WebError(HTTPStatus.NOT_FOUND, "Category not found.")
            kept_categories = [category for category in categories if category is not removed]
            moved = 0
            for pattern in items:
                if (
                    isinstance(pattern, dict)
                    and str(pattern.get("ownerId") or "") == owner_id
                    and str(pattern.get("categoryId") or "") == category_id
                ):
                    pattern["categoryId"] = DEFAULT_PATTERN_CATEGORY_ID
                    moved += 1
            self.store.set(
                "patterns", {"patterns": items, "categories": kept_categories}
            )
            self.append_bot_log(
                "pattern.category.delete",
                account_id=account_id,
                detail=str(removed.get("name") or category_id),
                data={"patternsMoved": moved},
            )
        return {"ok": True, "patternsMoved": moved}

    def create_pattern(
        self, body: dict[str, Any], user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        text = str(body.get("text") or "")
        category_id = str(
            body.get("categoryId") or DEFAULT_PATTERN_CATEGORY_ID
        ).strip()
        if not name:
            raise WebError(HTTPStatus.BAD_REQUEST, "Pattern name is required.")
        if not text.strip():
            raise WebError(HTTPStatus.BAD_REQUEST, "Pattern text is required.")
        with self.schedule_lock:
            data = self.store.get("patterns", {"patterns": []})
            items = data.get("patterns", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            categories = data.get("categories", []) if isinstance(data, dict) else []
            if not isinstance(categories, list):
                categories = []
            owner_id = str((user or {}).get("id") or "")
            category_exists = category_id == DEFAULT_PATTERN_CATEGORY_ID or any(
                isinstance(category, dict)
                and str(category.get("id") or "") == category_id
                and str(category.get("ownerId") or "") == owner_id
                for category in categories
            )
            if not category_exists:
                raise WebError(HTTPStatus.BAD_REQUEST, "Pattern category is invalid.")
            item = {
                "id": uuid.uuid4().hex,
                "ownerId": owner_id,
                "name": name,
                "text": text,
                "categoryId": category_id,
            }
            items.append(item)
            self.store.set(
                "patterns", {"patterns": items, "categories": categories}
            )
            self.append_bot_log(
                "pattern.create",
                account_id=str(body.get("accountId") or ""),
                detail=name,
            )
        return {"ok": True, "pattern": item}

    def delete_pattern(
        self,
        pattern_id: str,
        account_id: str | None = None,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.schedule_lock:
            data = self.store.get("patterns", {"patterns": []})
            items = data.get("patterns", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            owner_id = str((user or {}).get("id") or "")
            removed = next(
                (
                    p
                    for p in items
                    if isinstance(p, dict)
                    and p.get("id") == pattern_id
                    and str(p.get("ownerId") or "") == owner_id
                ),
                None,
            )
            kept = [p for p in items if p is not removed]
            if len(kept) == len(items):
                raise WebError(HTTPStatus.NOT_FOUND, "Pattern not found.")
            categories = data.get("categories", []) if isinstance(data, dict) else []
            self.store.set(
                "patterns", {"patterns": kept, "categories": categories}
            )
            self.append_bot_log(
                "pattern.delete",
                account_id=account_id,
                detail=str((removed or {}).get("name") or pattern_id),
            )
        return {"ok": True}

    # -- AI image settings (multi-provider) ------------------------------
    def _ai_all(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        root = self.store.get("ai_settings", {"tenants": {}})
        if not isinstance(root, dict):
            root = {"tenants": {}}
        owner_id = str((user or {}).get("id") or "")
        if "tenants" not in root:
            legacy = dict(root)
            root = {"tenants": {owner_id: legacy} if legacy else {}}
            self.store.set("ai_settings", root)
        tenants = root.get("tenants") if isinstance(root.get("tenants"), dict) else {}
        data = tenants.get(owner_id) if isinstance(tenants.get(owner_id), dict) else {}
        # Migrate the old flat Google-only shape {apiKey, model, baseUrl}.
        if "provider" not in data and any(k in data for k in ("apiKey", "model", "baseUrl")):
            data = {
                "provider": "google",
                "google": {
                    "apiKey": data.get("apiKey", ""),
                    "model": data.get("model", ""),
                    "baseUrl": data.get("baseUrl", ""),
                },
            }
        return data

    @staticmethod
    def _ai_provider_view(data: dict[str, Any], provider: str) -> dict[str, Any]:
        meta = _ai_provider_meta(provider)
        raw = data.get(provider)
        cfg = raw if isinstance(raw, dict) else {}
        model = str(cfg.get("model") or meta["model"]).strip() or meta["model"]
        view = {
            "apiKey": str(cfg.get("apiKey") or ""),
            "model": model,
            "baseUrl": str(cfg.get("baseUrl") or meta["baseUrl"]).strip() or meta["baseUrl"],
        }
        if provider == "nanobananaapi":
            aspect = str(cfg.get("aspectRatio") or "").strip()
            # Previous builds stored the aspect ratio in "model" for this
            # provider. Preserve that setting while moving model selection to
            # the actual endpoint chooser.
            if model in NBAPI_ASPECT_RATIOS:
                aspect = aspect or model
                model = NBAPI_DEFAULT_MODEL
            if model not in NBAPI_MODELS:
                model = NBAPI_DEFAULT_MODEL
            if aspect not in NBAPI_ASPECT_RATIOS:
                aspect = meta["aspectRatio"]
            view["model"] = model
            view["aspectRatio"] = aspect
        elif provider == "fal":
            if model not in FAL_MODELS:
                model = FAL_DEFAULT_MODEL
            image_size = str(cfg.get("aspectRatio") or meta["aspectRatio"]).strip()
            if image_size not in FAL_IMAGE_SIZES:
                image_size = FAL_DEFAULT_IMAGE_SIZE
            view["model"] = model
            view["aspectRatio"] = image_size
        return view

    def _active_provider(self, data: dict[str, Any]) -> str:
        provider = str(data.get("provider") or "google")
        return provider if provider in AI_PROVIDERS else "google"

    def ai_settings(
        self, user: dict[str, Any] | None = None, *, reveal: bool = False
    ) -> dict[str, Any]:
        data = self._ai_all(user)
        provider = self._active_provider(data)
        if reveal:
            # Config for the ACTIVE provider, used to actually generate.
            return {"provider": provider, **self._ai_provider_view(data, provider)}
        providers: dict[str, Any] = {}
        for name in AI_PROVIDERS:
            meta = _ai_provider_meta(name)
            view = self._ai_provider_view(data, name)
            providers[name] = {
                "hasApiKey": bool(view["apiKey"]),
                "apiKeyPreview": _mask_secret(view["apiKey"]),
                "model": view["model"],
                "baseUrl": view["baseUrl"],
                "models": list(meta["models"]),
                "defaultModel": meta["model"],
                "modelLabels": dict(meta.get("modelLabels") or {}),
                "modelPrices": dict(meta.get("modelPrices") or {}),
                "aspectRatio": view.get("aspectRatio"),
                "aspectRatios": list(meta.get("aspectRatios") or []),
                "aspectRatioLabels": dict(meta.get("aspectRatioLabels") or {}),
                "defaultAspectRatio": meta.get("aspectRatio"),
                "label": meta["label"],
                "modelLabel": meta["modelLabel"],
            }
        return {
            "provider": provider,
            "providers": providers,
            "configured": providers[provider]["hasApiKey"],
        }

    def save_ai_settings(
        self, body: dict[str, Any], user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Read-modify-write under one lock so a concurrent clear+save can't
        # resurrect a key the admin just removed (self.lock is reentrant).
        with self.lock:
            data = self._ai_all(user)
            root = self.store.get("ai_settings", {"tenants": {}})
            if not isinstance(root, dict):
                root = {"tenants": {}}
            tenants = root.get("tenants")
            if not isinstance(tenants, dict):
                tenants = {}
                root["tenants"] = tenants
            owner_id = str((user or {}).get("id") or "")
            requested = str(body.get("provider") or "")
            provider = requested if requested in AI_PROVIDERS else self._active_provider(data)
            meta = _ai_provider_meta(provider)
            current = self._ai_provider_view(data, provider)
            raw_key = body.get("apiKey")
            if body.get("clearApiKey"):
                api_key = ""
            elif raw_key is None or not str(raw_key).strip():
                # Blank key on save keeps the stored one (edit model without re-typing).
                api_key = current["apiKey"]
            else:
                api_key = str(raw_key).strip()
            model = str(body.get("model") or current["model"]).strip() or meta["model"]
            base_url = str(body.get("baseUrl") or current["baseUrl"]).strip() or meta["baseUrl"]
            record = {"apiKey": api_key, "model": model, "baseUrl": base_url}
            if provider == "nanobananaapi":
                legacy_aspect = model if model in NBAPI_ASPECT_RATIOS else ""
                if model not in NBAPI_MODELS:
                    model = NBAPI_DEFAULT_MODEL
                aspect_ratio = str(
                    body.get("aspectRatio")
                    or legacy_aspect
                    or current.get("aspectRatio")
                    or meta["aspectRatio"]
                ).strip()
                if aspect_ratio not in NBAPI_ASPECT_RATIOS:
                    aspect_ratio = meta["aspectRatio"]
                record["model"] = model
                record["aspectRatio"] = aspect_ratio
            elif provider == "fal":
                if model not in FAL_MODELS:
                    model = FAL_DEFAULT_MODEL
                image_size = str(
                    body.get("aspectRatio")
                    or current.get("aspectRatio")
                    or meta["aspectRatio"]
                ).strip()
                if image_size not in FAL_IMAGE_SIZES:
                    image_size = FAL_DEFAULT_IMAGE_SIZE
                record["model"] = model
                record["aspectRatio"] = image_size
            data[provider] = record
            # A save selects the active provider; a bare "clear" must not switch it.
            if not body.get("clearApiKey"):
                data["provider"] = provider
            tenants[owner_id] = data
            self.store.set("ai_settings", root)
            return {"ok": True, **self.ai_settings(user)}

    def generate_ai_image(
        self, prompt: str, user: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = self.ai_settings(user, reveal=True)
        result = _generate_ai_image(
            _prepare_ai_image_prompt(prompt),
            provider=cfg["provider"],
            api_key=cfg["apiKey"],
            model=cfg["model"],
            aspect_ratio=str(cfg.get("aspectRatio") or ""),
            base_url=cfg["baseUrl"],
        )
        return {
            "ok": True,
            "image": base64.b64encode(result["data"]).decode("ascii"),
            "mime": result["mime"],
            "name": result["name"],
        }

    def disable_schedules_for_account(self, account_id: str) -> None:
        with self.schedule_lock:
            changed = False
            for job in self.schedules:
                if job.get("accountId") == account_id:
                    job["enabled"] = False
                    job["status"] = "account deleted"
                    changed = True
            if changed:
                self.save_schedules()

    def run_schedule_now(
        self, schedule_id: str, account_id: str | None = None
    ) -> dict[str, Any]:
        return self._execute_schedule(schedule_id, manual=True, account_id=account_id)

    def run_due_schedules(self) -> None:
        now = time.time()
        due: list[str] = []
        changed = False
        with self.schedule_lock:
            for job in self.schedules:
                if not job.get("enabled") or job.get("running"):
                    continue
                next_run = job.get("nextRunAt")
                if not next_run:
                    job["nextRunAt"] = _compute_next_epoch(job)
                    next_run = job.get("nextRunAt")
                    if not next_run:
                        job["enabled"] = False
                        job["status"] = "completed"
                    changed = True
                if next_run and float(next_run) <= now:
                    due.append(str(job["id"]))
            if changed:
                self.save_schedules()
        for schedule_id in due:
            self._execute_schedule(schedule_id, manual=False)

    def _find_schedule(self, schedule_id: str) -> dict[str, Any]:
        for job in self.schedules:
            if job.get("id") == schedule_id:
                return job
        raise WebError(HTTPStatus.NOT_FOUND, "Schedule not found.")

    def _execute_schedule(
        self, schedule_id: str, *, manual: bool, account_id: str | None = None
    ) -> dict[str, Any]:
        with self.schedule_lock:
            job = self._find_schedule(schedule_id)
            _ensure_schedule_account(job, account_id)
            if job.get("running"):
                raise WebError(HTTPStatus.CONFLICT, "Schedule is already running.")
            job["running"] = True
            job["status"] = "running"
            self.save_schedules()
            snapshot = dict(job)

        try:
            account_id = str(snapshot.get("accountId") or "")

            def log_bot(
                action: str,
                detail: str = "",
                ok: bool = True,
                data: dict[str, Any] | None = None,
            ) -> None:
                self.append_bot_log(
                    action,
                    account_id=account_id,
                    schedule=snapshot,
                    ok=ok,
                    detail=detail,
                    data=data,
                )

            log_bot(
                "schedule.run.start",
                "manual" if manual else "scheduled",
                data={"manual": manual},
            )
            api = self.get_api(account_id)
            owner = self.web_auth.user_for_account(account_id)
            with self.api_lock:
                to = _resolve_to(api, str(snapshot.get("to") or ""))
                message_ids = _send_job_contents(
                    api,
                    to,
                    snapshot,
                    ai_settings=self.ai_settings(owner, reveal=True),
                    log=log_bot,
                )
            message_id = message_ids[-1] if message_ids else None
            sent_at = _now_iso()
            with self.schedule_lock:
                job = self._find_schedule(schedule_id)
                job["running"] = False
                job["sentCount"] = int(job.get("sentCount") or 0) + 1
                job["lastRunAt"] = sent_at
                job["lastMessageId"] = message_id
                job["lastError"] = None
                _append_history(job, {"at": sent_at, "ok": True, "messageIds": message_ids})
                if not manual:
                    _advance_after_success(job)
                else:
                    job["status"] = "waiting" if job.get("enabled") else "manual"
                self.save_schedules()
                log_bot(
                    "schedule.run.success",
                    f"{len(message_ids)} message(s)",
                    data={"manual": manual, "messageIds": message_ids},
                )
                return {"ok": True, "schedule": dict(job), "messageIds": message_ids}
        except Exception as exc:
            try:
                account_id = str(snapshot.get("accountId") or "")
                self.append_bot_log(
                    "schedule.run.error",
                    account_id=account_id,
                    schedule=snapshot,
                    ok=False,
                    detail=str(exc),
                    data={"manual": manual},
                )
            except Exception:
                pass
            with self.schedule_lock:
                job = self._find_schedule(schedule_id)
                job["running"] = False
                job["lastError"] = str(exc)
                job["status"] = "error"
                _append_history(job, {"at": _now_iso(), "ok": False, "error": str(exc)})
                if not manual:
                    _advance_after_error(job)
                self.save_schedules()
            if isinstance(exc, WebError):
                raise
            raise WebError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc


class OkLineWebHandler(BaseHTTPRequestHandler):
    server: OkLineWebServer

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def state(self) -> WebState:
        return self.server.state

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            if method == "GET" and parsed.path == "/":
                return self._html(INDEX_HTML)
            if method == "GET" and parsed.path in {"/god", "/god/"}:
                return self._html(GOD_HTML)
            if parsed.path == "/favicon.ico":
                return self._bytes(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
            self.current_user: dict[str, Any] | None = None
            god_public_paths = {
                "/api/god/status",
                "/api/god/login",
                "/api/god/logout",
            }
            if parsed.path.startswith("/api/god/"):
                if parsed.path not in god_public_paths:
                    self.current_user = self.state.web_auth.require_user(
                        self.headers.get("cookie"),
                        cookie_name=WebAuth.god_cookie_name,
                    )
                    if not self.state.web_auth._is_god(self.current_user):
                        raise WebError(
                            HTTPStatus.FORBIDDEN,
                            "God authentication required.",
                            "god_auth_required",
                        )
            elif parsed.path.startswith("/api/") and not parsed.path.startswith(
                "/api/auth/"
            ):
                self.current_user = self.state.web_auth.require_user(
                    self.headers.get("cookie")
                )
                if self.state.web_auth._is_god(self.current_user):
                    raise WebError(
                        HTTPStatus.FORBIDDEN,
                        "Use the God portal at /god.",
                        "god_portal_only",
                    )
            if method == "GET" and parsed.path == "/api/message-content":
                return self._message_content(parse_qs(parsed.query))
            data = self._dispatch(method, parsed.path, parse_qs(parsed.query))
            if isinstance(data, WebResult):
                return self._json(data.data, data.status, headers=data.headers)
            return self._json(data)
        except WebError as exc:
            return self._json({"error": exc.message, "code": exc.code}, exc.status)
        except LineLoginRequired as exc:
            return self._json(
                {"error": str(exc), "code": "line_login_required"},
                HTTPStatus.UNAUTHORIZED,
            )
        except Exception as exc:
            # Log the raw exception to the server console for the operator, but
            # hand the browser a stable, friendly, localisable code instead of a
            # raw stack-trace string.
            traceback.print_exc()
            return self._json(
                {
                    "error": "Unexpected server error.",
                    "code": "server_error",
                    "detail": str(exc),
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _dispatch(self, method: str, path: str, query: dict[str, list[str]]) -> Any:
        if method == "GET" and path == "/api/god/status":
            auth = self.state.web_auth
            user = auth.current_user(
                self.headers.get("cookie"), cookie_name=WebAuth.god_cookie_name
            )
            if user is not None and not auth._is_god(user):
                auth.logout(
                    self.headers.get("cookie"), cookie_name=WebAuth.god_cookie_name
                )
                user = None
            return {
                "authenticated": bool(user),
                "user": auth.public_user(user),
            }
        if method == "POST" and path == "/api/god/login":
            body = self._read_json()
            token = self.state.web_auth.login_god(
                str(body.get("username") or body.get("email") or ""),
                str(body.get("password") or ""),
            )
            user = self.state.web_auth.current_user(
                WebAuth.cookie_header(token, cookie_name=WebAuth.god_cookie_name),
                cookie_name=WebAuth.god_cookie_name,
            )
            return WebResult(
                {"ok": True, "authenticated": True, "user": WebAuth.public_user(user)},
                headers=[
                    (
                        "Set-Cookie",
                        WebAuth.cookie_header(
                            token, cookie_name=WebAuth.god_cookie_name
                        ),
                    )
                ],
            )
        if method == "POST" and path == "/api/god/logout":
            self.state.web_auth.logout(
                self.headers.get("cookie"), cookie_name=WebAuth.god_cookie_name
            )
            return WebResult(
                {"ok": True},
                headers=[
                    (
                        "Set-Cookie",
                        WebAuth.clear_cookie_header(
                            cookie_name=WebAuth.god_cookie_name
                        ),
                    )
                ],
            )
        if method == "GET" and path == "/api/god/users":
            return self.state.god_users_payload()
        if method == "GET" and path == "/api/god/users/detail":
            return self.state.user_detail(
                _query_one(query, "userId", ""), self.current_user
            )
        if method == "POST" and path == "/api/god/users/update":
            return self.state.update_user(self._read_json(), self.current_user)
        if method == "POST" and path == "/api/god/users/delete":
            return self.state.delete_user(self._read_json(), self.current_user or {})
        if method == "GET" and path == "/api/auth/status":
            auth = self.state.web_auth
            token = auth.ensure_simple_mode() if not auth.configured() else None
            cookie = WebAuth.cookie_header(token) if token else self.headers.get("cookie")
            status = auth.status(cookie)
            if (status.get("user") or {}).get("role") == "god":
                auth.logout(cookie)
                status = auth.status(None)
                return WebResult(
                    status,
                    headers=[("Set-Cookie", WebAuth.clear_cookie_header())],
                )
            if token:
                return WebResult(
                    status, headers=[("Set-Cookie", WebAuth.cookie_header(token))]
                )
            return status
        if method == "POST" and path == "/api/auth/secure":
            user = self.state.web_auth.require_user(self.headers.get("cookie"))
            self.state.web_auth.require_permission(user, "change_password")
            body = self._read_json()
            self.state.web_auth.secure_with_password(
                str(body.get("email") or body.get("username") or ""),
                str(body.get("password") or ""),
            )
            return {"ok": True, "simpleMode": False}
        if method == "POST" and path == "/api/auth/setup":
            body = self._read_json()
            token = self.state.web_auth.setup(
                str(body.get("email") or body.get("username") or ""),
                str(body.get("password") or ""),
            )
            return WebResult(
                {
                    "ok": True,
                    "configured": True,
                    "authenticated": True,
                    "user": self.state.web_auth.public_user(
                        self.state.web_auth.current_user(WebAuth.cookie_header(token))
                    ),
                    "roles": self.state.web_auth.roles_payload(),
                },
                headers=[("Set-Cookie", WebAuth.cookie_header(token))],
            )
        if method == "POST" and path == "/api/auth/login":
            body = self._read_json()
            token = self.state.web_auth.login(
                str(body.get("email") or body.get("username") or ""),
                str(body.get("password") or ""),
            )
            return WebResult(
                {
                    "ok": True,
                    "configured": True,
                    "authenticated": True,
                    "user": self.state.web_auth.public_user(
                        self.state.web_auth.current_user(WebAuth.cookie_header(token))
                    ),
                    "roles": self.state.web_auth.roles_payload(),
                },
                headers=[("Set-Cookie", WebAuth.cookie_header(token))],
            )
        if method == "POST" and path == "/api/auth/register":
            body = self._read_json()
            token = self.state.web_auth.register(
                str(body.get("email") or body.get("username") or ""),
                str(body.get("password") or ""),
                str(body.get("displayName") or ""),
            )
            return WebResult(
                {
                    "ok": True,
                    "configured": True,
                    "authenticated": True,
                    "user": self.state.web_auth.public_user(
                        self.state.web_auth.current_user(WebAuth.cookie_header(token))
                    ),
                    "roles": self.state.web_auth.roles_payload(),
                },
                headers=[("Set-Cookie", WebAuth.cookie_header(token))],
            )
        if method == "POST" and path == "/api/auth/change-password":
            user = self.state.web_auth.require_user(self.headers.get("cookie"))
            body = self._read_json()
            self.state.web_auth.change_password(
                user,
                str(body.get("currentPassword") or ""),
                str(body.get("newPassword") or ""),
            )
            return {"ok": True}
        if method == "POST" and path == "/api/auth/logout":
            self.state.web_auth.logout(self.headers.get("cookie"))
            return WebResult(
                {"ok": True},
                headers=[("Set-Cookie", WebAuth.clear_cookie_header())],
            )
        if method == "GET" and path == "/api/status":
            return self.state.status(_query_one(query, "accountId", ""), self.current_user)
        if method == "GET" and path == "/api/accounts":
            return self.state.accounts(self.current_user)
        if method == "POST" and path == "/api/accounts/switch":
            body = self._read_json()
            return self.state.switch_account(
                str(body.get("accountId") or ""), self.current_user
            )
        if method == "POST" and path == "/api/accounts/update":
            self._require_permission("manage_accounts")
            body = self._read_json()
            self._require_account_access(str(body.get("accountId") or ""))
            return self.state.update_account(
                str(body.get("accountId") or ""),
                str(body.get("label") or ""),
                self.current_user,
            )
        if method == "POST" and path == "/api/accounts/delete":
            self._require_permission("manage_accounts")
            body = self._read_json()
            self._require_account_access(str(body.get("accountId") or ""))
            return self.state.delete_account(
                str(body.get("accountId") or ""), self.current_user
            )
        if method == "GET" and path == "/api/users":
            self._require_permission("manage_users")
            return self.state.users_payload()
        if method == "GET" and path == "/api/users/detail":
            self._require_permission("manage_users")
            return self.state.user_detail(
                _query_one(query, "userId", ""), self.current_user
            )
        if method == "POST" and path == "/api/users/create":
            self._require_permission("manage_users")
            return self.state.create_user(self._read_json(), self.current_user)
        if method == "POST" and path == "/api/users/update":
            self._require_permission("manage_users")
            return self.state.update_user(self._read_json(), self.current_user)
        if method == "POST" and path == "/api/users/delete":
            self._require_permission("manage_users")
            return self.state.delete_user(self._read_json(), self.current_user or {})
        if method == "POST" and path == "/api/login/start":
            self._require_permission("manage_accounts")
            body = self._read_json()
            return self.state.start_login(
                float(body.get("waitSeconds") or 180.0),
                account_name=str(body.get("accountName") or "").strip() or None,
                user=self.current_user,
            )
        if method == "GET" and path == "/api/login/state":
            self._require_permission("manage_accounts")
            return self.state.login_state(self.current_user)
        if method == "POST" and path == "/api/login/cancel":
            self._require_permission("manage_accounts")
            return self.state.cancel_login(self.current_user)
        if method == "POST" and path == "/api/logout":
            self._require_permission("manage_accounts")
            body = self._read_json()
            self._require_account_access(str(body.get("accountId") or ""))
            return self.state.logout_account(
                str(body.get("accountId") or ""), self.current_user
            )
        if method == "GET" and path == "/api/patterns":
            self._require_permission("read")
            return self.state.list_patterns(self.current_user)
        if method == "POST" and path == "/api/patterns/create":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = str(body.get("accountId") or "")
            if account_id:
                self._require_account_access(account_id)
            return self.state.create_pattern(body, self.current_user)
        if method == "POST" and path == "/api/patterns/delete":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = str(body.get("accountId") or "")
            if account_id:
                self._require_account_access(account_id)
            return self.state.delete_pattern(
                str(body.get("id") or ""), account_id or None, self.current_user
            )
        if method == "POST" and path == "/api/pattern-categories/create":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = str(body.get("accountId") or "")
            if account_id:
                self._require_account_access(account_id)
            return self.state.create_pattern_category(body, self.current_user)
        if method == "POST" and path == "/api/pattern-categories/delete":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = str(body.get("accountId") or "")
            if account_id:
                self._require_account_access(account_id)
            return self.state.delete_pattern_category(
                str(body.get("id") or ""), account_id or None, self.current_user
            )
        if method == "POST" and path == "/api/pattern-categories/update":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = str(body.get("accountId") or "")
            if account_id:
                self._require_account_access(account_id)
            return self.state.update_pattern_category(body, self.current_user)
        if method == "GET" and path == "/api/bot/logs":
            self._require_permission("read")
            account_id = _required_account_id(_query_one(query, "accountId", ""))
            self._require_account_access(account_id)
            limit = _query_int(query, "limit", 200, minimum=1, maximum=500)
            return self.state.list_bot_logs(account_id, self.current_user, limit=limit)
        if method == "POST" and path == "/api/bot/logs/clear":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = _required_account_id(str(body.get("accountId") or ""))
            self._require_account_access(account_id)
            return self.state.clear_bot_logs(account_id, self.current_user)
        if method == "GET" and path == "/api/schedules":
            self._require_permission("read")
            account_id = _required_account_id(_query_one(query, "accountId", ""))
            return self.state.list_schedules(account_id, self.current_user)
        if method == "POST" and path == "/api/schedules/create":
            self._require_permission("schedule")
            body = self._read_json()
            self._require_account_access(str(body.get("accountId") or ""))
            return self.state.create_schedule(body)
        if method == "POST" and path == "/api/schedules/update":
            self._require_permission("schedule")
            body = self._read_json()
            self._require_account_access(str(body.get("accountId") or ""))
            return self.state.update_schedule(str(body.get("id") or ""), body)
        if method == "POST" and path == "/api/schedules/toggle":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = _required_account_id(str(body.get("accountId") or ""))
            self._require_account_access(account_id)
            return self.state.toggle_schedule(
                str(body.get("id") or ""), bool(body.get("enabled")), account_id
            )
        if method == "POST" and path == "/api/schedules/delete":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = _required_account_id(str(body.get("accountId") or ""))
            self._require_account_access(account_id)
            return self.state.delete_schedule(str(body.get("id") or ""), account_id)
        if method == "POST" and path == "/api/schedules/run-now":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = _required_account_id(str(body.get("accountId") or ""))
            self._require_account_access(account_id)
            return self.state.run_schedule_now(str(body.get("id") or ""), account_id)
        if method == "POST" and path == "/api/schedules/clear-stuck":
            self._require_permission("schedule")
            body = self._read_json()
            account_id = _required_account_id(str(body.get("accountId") or ""))
            self._require_account_access(account_id)
            return self.state.clear_stuck_schedules(account_id, self.current_user)
        if method == "GET" and path == "/api/contacts":
            self._require_permission("read")
            return self._with_api(query, lambda api: self._contacts(api, query))
        if method == "GET" and path == "/api/groups":
            self._require_permission("read")
            return self._with_api(query, lambda api: self._groups(api, query))
        if method == "GET" and path == "/api/boxes":
            self._require_permission("read")
            return self._with_api(query, lambda api: self._boxes(api, query))
        if method == "GET" and path == "/api/messages":
            self._require_permission("read")
            return self._with_api(query, lambda api: self._messages(api, query))
        if method == "GET" and path == "/api/find-user":
            self._require_permission("tools")
            return self._with_api(query, lambda api: self._find_user(api, query))
        if method == "POST" and path == "/api/send":
            self._require_permission("send")
            body = self._read_json()
            return self._with_api(body, lambda api: self._send(api, body))
        if method == "POST" and path == "/api/send-media":
            self._require_permission("send")
            body = self._read_json()
            return self._with_api(body, lambda api: self._send_media(api, body))
        if method == "POST" and path == "/api/call":
            self._require_permission("tools")
            body = self._read_json()
            return self._with_api(body, lambda api: self._call(api, body))
        if method == "GET" and path == "/api/ai/settings":
            self._require_permission("read")
            return self.state.ai_settings(self.current_user)
        if method == "POST" and path == "/api/ai/settings":
            self._require_permission("manage_accounts")
            return self.state.save_ai_settings(self._read_json(), self.current_user)
        if method == "POST" and path == "/api/ai/generate":
            self._require_permission("schedule")
            body = self._read_json()
            return self.state.generate_ai_image(
                str(body.get("prompt") or ""), self.current_user
            )
        raise WebError(HTTPStatus.NOT_FOUND, "Unknown route.")

    def _require_permission(self, permission: str) -> None:
        self.state.web_auth.require_permission(self.current_user, permission)

    def _require_account_access(self, account_id: str) -> None:
        account_id = _required_account_id(account_id)
        if not self.state.web_auth.can_access_account(self.current_user, account_id):
            raise WebError(HTTPStatus.FORBIDDEN, "Permission denied for this LINE account.")

    def _with_api(self, source: dict[str, Any] | dict[str, list[str]], fn):
        raw_account_id = source.get("accountId") if isinstance(source, dict) else ""
        if isinstance(raw_account_id, list):
            raw_account_id = raw_account_id[0] if raw_account_id else ""
        account_id = _required_account_id(str(raw_account_id or ""))
        self._require_account_access(account_id)
        if not self.state.account_store.get(account_id):
            raise WebError(HTTPStatus.NOT_FOUND, "Account not found.")
        api = self.state.get_api(account_id)
        with self.state.api_lock:
            return fn(api)

    def _contacts(self, api: OkLine, query: dict[str, list[str]]) -> dict[str, Any]:
        search = _query_one(query, "search", "").lower()
        limit = _query_int(query, "limit", 250, minimum=1, maximum=1000)
        recency = _chat_recency_map(api)
        rows = []
        for _mid, contact in _contact_rows(api):
            name = contact.get("name") or ""
            if search and search not in name.lower():
                continue
            rows.append(contact)
        rows.sort(key=lambda row: _recent_chat_sort_key(row, recency))
        rows = rows[:limit]
        return {"contacts": rows, "count": len(rows)}

    def _groups(self, api: OkLine, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = _query_int(query, "limit", 200, minimum=1, maximum=500)
        chat_mids = api.get_all_chat_mids() or {}
        mids = list(chat_mids.get("memberChatMids", []) or [])
        invited = set(chat_mids.get("invitedChatMids", []) or [])
        mids.extend([m for m in invited if m not in mids])
        recency = _chat_recency_map(api)
        groups = []
        for raw in api.get_chats(mids).get("chats", []) if mids else []:
            grp = Group.from_dict(raw)
            groups.append(
                {
                    "mid": grp.chat_mid,
                    "name": grp.name,
                    "memberCount": grp.member_count,
                    "invited": grp.chat_mid in invited,
                }
            )
        groups.sort(key=lambda row: _recent_chat_sort_key(row, recency))
        groups = groups[:limit]
        return {"groups": groups, "count": len(groups)}

    def _boxes(self, api: OkLine, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = _query_int(query, "limit", 20, minimum=1, maximum=100)
        boxes = api.get_message_boxes(limit=limit)
        out = []
        for box in boxes.get("messageBoxes", []) if isinstance(boxes, dict) else []:
            if isinstance(box, dict):
                out.append(
                    {
                        "id": box.get("id"),
                        "unreadCount": box.get("unreadCount"),
                        "lastMessages": box.get("lastMessages", []),
                    }
                )
        return {"boxes": out, "count": len(out)}

    def _messages(self, api: OkLine, query: dict[str, list[str]]) -> dict[str, Any]:
        chat_mid = _query_one(query, "chat_mid")
        if not chat_mid:
            raise WebError(HTTPStatus.BAD_REQUEST, "chat_mid is required.")
        count = _query_int(query, "count", 30, minimum=1, maximum=200)
        names = _contact_name_map(api)
        # our own display name, so our own messages don't show a raw mid
        try:
            profile = api.get_profile()
            if isinstance(profile, dict) and profile.get("mid"):
                names.setdefault(profile["mid"], profile.get("displayName") or "")
        except Exception:
            pass
        messages = api.get_recent_messages(chat_mid, count) or []
        # resolve senders we don't know yet (e.g. group members who aren't in
        # our own contact list) to their real LINE display names
        _resolve_names(
            api,
            [m["from"] for m in messages if isinstance(m, dict) and m.get("from")],
            names,
        )
        return {
            "messages": [_message_summary(api, msg, names) for msg in reversed(messages)],
            "count": len(messages),
        }

    def _find_user(self, api: OkLine, query: dict[str, list[str]]) -> dict[str, Any]:
        userid = _query_one(query, "userid")
        if not userid:
            raise WebError(HTTPStatus.BAD_REQUEST, "userid is required.")
        result = api.find_contact_by_userid(userid) or {}
        return {"contact": result}

    def _send(self, api: OkLine, body: dict[str, Any]) -> dict[str, Any]:
        to = _resolve_to(api, str(body.get("to") or ""))
        text = str(body.get("text") or "")
        if not text:
            raise WebError(HTTPStatus.BAD_REQUEST, "text is required.")
        encrypt = bool(body.get("encrypt"))
        result = api.send_encrypted_text(to, text) if encrypt else api.send_text(to, text)
        message_id = result.get("id") if isinstance(result, dict) else result
        return {"ok": True, "messageId": message_id, "result": result}

    def _send_media(self, api: OkLine, body: dict[str, Any]) -> dict[str, Any]:
        to = _resolve_to(api, str(body.get("to") or ""))
        data_b64 = str(body.get("data") or "")
        if "," in data_b64 and data_b64.lstrip().startswith("data:"):
            data_b64 = data_b64.split(",", 1)[1]  # strip a data: URL prefix
        if not data_b64:
            raise WebError(HTTPStatus.BAD_REQUEST, "file data is required.")
        try:
            raw = base64.b64decode(data_b64)
        except ValueError as exc:
            raise WebError(HTTPStatus.BAD_REQUEST, f"Invalid file data: {exc}") from exc
        if not raw:
            raise WebError(HTTPStatus.BAD_REQUEST, "file is empty.")
        if len(raw) > 30 * 1024 * 1024:
            raise WebError(HTTPStatus.BAD_REQUEST, "File is too large (max 30 MB).")
        name = str(body.get("filename") or "").strip() or "upload"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        is_image = str(body.get("kind") or "").lower() == "image" or ext in {
            "jpg",
            "jpeg",
            "png",
            "gif",
            "webp",
        }
        if is_image:
            result = api.send_image(to, raw, name=name)
        else:
            result = api.send_file(to, raw, name=name)
        message_id = result.get("id") if isinstance(result, dict) else result
        return {"ok": True, "messageId": message_id, "result": result}

    def _message_content(self, query: dict[str, list[str]]) -> None:
        """Proxy the raw bytes of a chat image/media object out of OBS so the
        browser can display it inline (OBS needs the signed session)."""
        self._require_permission("read")
        message_id = _query_one(query, "message_id")
        if not message_id:
            raise WebError(HTTPStatus.BAD_REQUEST, "message_id is required.")
        try:
            data = self._with_api(
                query, lambda api: api.obs.download_object("talk", "m", message_id)
            )
        except WebError:
            raise
        except Exception as exc:
            raise WebError(HTTPStatus.NOT_FOUND, "Media not available.") from exc
        self._bytes(data, _sniff_content_type(data), HTTPStatus.OK)

    def _call(self, api: OkLine, body: dict[str, Any]) -> dict[str, Any]:
        endpoint = str(body.get("endpoint") or "")
        args = body.get("args")
        if not endpoint:
            raise WebError(HTTPStatus.BAD_REQUEST, "endpoint is required.")
        if not isinstance(args, list):
            raise WebError(HTTPStatus.BAD_REQUEST, "args must be a JSON array.")
        return {"result": api.transport.call(endpoint, args)}

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise WebError(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise WebError(HTTPStatus.BAD_REQUEST, "JSON body must be an object.")
        return data

    def _json(
        self,
        data: Any,
        status: int = HTTPStatus.OK,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8")
        self._bytes(payload, "application/json; charset=utf-8", status, headers=headers)

    def _html(self, html: str) -> None:
        self._bytes(html.encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.OK)

    def _bytes(
        self,
        payload: bytes,
        content_type: str,
        status: int,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("pragma", "no-cache")
        self.send_header("expires", "0")
        for key, value in headers or []:
            self.send_header(key, value)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


class OkLineWebServer(ThreadingHTTPServer):
    state: WebState


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    tokens_file: str = "tokens.json",
    state_dir: str = ".okline",
    accounts_file: str | None = None,
    accounts_dir: str | None = None,
    schedules_file: str | None = None,
    auth_file: str | None = None,
    database_url: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    show_secrets: bool = False,
    open_browser: bool = True,
) -> int:
    accounts_file = accounts_file or str(Path(state_dir) / "accounts.json")
    accounts_dir = accounts_dir or str(Path(state_dir) / "accounts")
    schedules_file = schedules_file or str(Path(state_dir) / "schedules.json")
    auth_file = auth_file or str(Path(state_dir) / "auth.json")
    config = WebConfig(
        host=host,
        port=port,
        tokens_file=tokens_file,
        state_dir=state_dir,
        accounts_file=accounts_file,
        accounts_dir=accounts_dir,
        schedules_file=schedules_file,
        auth_file=auth_file,
        database_url=database_url,
        access_token=access_token,
        refresh_token=refresh_token,
        show_secrets=show_secrets,
    )
    server = _bind_server(config)
    host = str(server.server_address[0])
    bound_port = server.server_address[1]
    url = f"http://{host}:{bound_port}/"
    print(f"LinePassport running at {url}")
    print(
        "Forgot the LinePassport password? It is separate from your LINE password "
        f"and cannot be recovered — delete {auth_file} to reset access, then reopen."
    )
    print("Press Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.state.close()
        server.server_close()
    return 0


def _bind_server(config: WebConfig) -> OkLineWebServer:
    ports = [config.port] if config.port == 0 else range(config.port, config.port + 20)
    last_error: OSError | None = None
    for candidate in ports:
        try:
            server = OkLineWebServer((config.host, candidate), OkLineWebHandler)
            server.state = WebState(
                WebConfig(
                    host=config.host,
                    port=server.server_address[1],
                    tokens_file=config.tokens_file,
                    state_dir=config.state_dir,
                    accounts_file=config.accounts_file,
                    accounts_dir=config.accounts_dir,
                    schedules_file=config.schedules_file,
                    auth_file=config.auth_file,
                    database_url=config.database_url,
                    access_token=config.access_token,
                    refresh_token=config.refresh_token,
                    show_secrets=config.show_secrets,
                )
            )
            return server
        except OSError as exc:
            last_error = exc
            if config.port == 0:
                break
    raise OSError(f"could not bind {config.host}:{config.port}: {last_error}")


def _sniff_content_type(data: bytes) -> str:
    """Best-effort image MIME type from the leading magic bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _query_one(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _required_account_id(account_id: str) -> str:
    account_id = (account_id or "").strip()
    if not account_id:
        raise WebError(HTTPStatus.BAD_REQUEST, "Select a LINE account first.", "no_account")
    return account_id


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _ensure_schedule_account(job: dict[str, Any], account_id: str | None) -> None:
    if account_id and job.get("accountId") != account_id:
        raise WebError(HTTPStatus.FORBIDDEN, "Schedule belongs to another account.")


def _short_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)>,]+")


def _safe_log_detail(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    lowered = text.lower()
    if "obs.line-apps.com" in lowered and (
        "ssleoferror" in lowered or "eof occurred" in lowered
    ):
        return "LINE media upload failed after retries (SSL EOF)."
    if "obs.line-apps.com" in lowered and "max retries exceeded" in lowered:
        return "LINE media upload failed after retries."
    text = _URL_IN_TEXT_RE.sub(lambda match: _safe_url(match.group(0)), text)
    return _short_text(text, limit)


def _bot_log_detail(action: Any, value: Any) -> str:
    if str(action or "") in {"content.ai.start", "content.ai.error"}:
        return str(value or "").strip()
    return _safe_log_detail(value)


def _query_int(
    query: dict[str, list[str]],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(_query_one(query, key, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _schedule_from_body(
    body: dict[str, Any], *, account_id: str, existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    mode = str(body.get("mode") or (existing or {}).get("mode") or "once").lower()
    if mode not in {"once", "repeat"}:
        raise WebError(HTTPStatus.BAD_REQUEST, "mode must be once or repeat.")
    content_source = str(
        body.get("contentSource") or (existing or {}).get("contentSource") or "text"
    ).lower()
    if content_source not in {"text", "image", "api", "ai_image"}:
        raise WebError(
            HTTPStatus.BAD_REQUEST,
            "contentSource must be text, image, api, or ai_image.",
        )
    text = str(body.get("text") or (existing or {}).get("text") or "")
    image_source = str(body.get("imageSource") or (existing or {}).get("imageSource") or "")
    image_data = str(body.get("imageData") or (existing or {}).get("imageData") or "")
    image_name = str(body.get("imageName") or (existing or {}).get("imageName") or "")
    if image_data and len(image_data) > 40 * 1024 * 1024:
        raise WebError(HTTPStatus.BAD_REQUEST, "Job image is too large (max 30 MB).")
    raw_pids = body.get("patternIds", (existing or {}).get("patternIds", []))
    raw_ptexts = body.get("patternTexts", (existing or {}).get("patternTexts", []))
    pattern_ids = [str(x) for x in raw_pids if x] if isinstance(raw_pids, list) else []
    pattern_texts = (
        [str(x) for x in raw_ptexts if str(x).strip()] if isinstance(raw_ptexts, list) else []
    )
    api_url = str(body.get("apiUrl") or (existing or {}).get("apiUrl") or "")
    api_method = str(
        body.get("apiMethod") or (existing or {}).get("apiMethod") or "GET"
    ).upper()
    api_body = str(body.get("apiBody") or (existing or {}).get("apiBody") or "")
    ai_prompt = str(body.get("aiPrompt") or (existing or {}).get("aiPrompt") or "")
    to = str(body.get("to") or (existing or {}).get("to") or "")
    if not to.strip():
        raise WebError(HTTPStatus.BAD_REQUEST, "target is required.")
    if content_source == "text" and not text.strip() and not pattern_texts:
        raise WebError(HTTPStatus.BAD_REQUEST, "message text is required.")
    if content_source == "image" and not image_source.strip() and not image_data.strip():
        raise WebError(HTTPStatus.BAD_REQUEST, "image source is required.")
    if content_source == "api" and not api_url.strip():
        raise WebError(HTTPStatus.BAD_REQUEST, "API URL is required.")
    if content_source == "ai_image" and not ai_prompt.strip() and not pattern_texts:
        raise WebError(HTTPStatus.BAD_REQUEST, "AI image prompt is required.")
    encrypt = bool(body.get("encrypt", (existing or {}).get("encrypt", False)))
    if encrypt and content_source in {"image", "ai_image"}:
        # Reject up front: images can't be Letter-Sealed, and for ai_image this
        # avoids paying for a generated image only to discard it at send time.
        raise WebError(
            HTTPStatus.BAD_REQUEST,
            "Images cannot be sent with E2EE. Turn off encryption for image content.",
        )
    if api_method not in {"GET", "POST"}:
        raise WebError(HTTPStatus.BAD_REQUEST, "apiMethod must be GET or POST.")

    interval = int(
        float(body.get("intervalMinutes") or (existing or {}).get("intervalMinutes") or 15)
    )
    interval = max(1, min(1440, interval))
    max_runs_raw = body.get(
        "maxRuns", (existing or {}).get("maxRuns", 1 if mode == "once" else 0)
    )
    max_runs = int(float(max_runs_raw or 0))
    if mode == "once":
        max_runs = 1

    run_at = str(body.get("runAt") or (existing or {}).get("runAt") or "")
    if mode == "once" and not run_at:
        raise WebError(HTTPStatus.BAD_REQUEST, "runAt is required for one-time schedules.")

    job = dict(existing or {})
    job.update(
        {
            "id": job.get("id") or uuid.uuid4().hex,
            "name": str(body.get("name") or job.get("name") or "Scheduled message"),
            "accountId": account_id,
            "to": to.strip(),
            "contentSource": content_source,
            "text": text,
            "patternIds": pattern_ids,
            "patternTexts": pattern_texts,
            "imageSource": image_source.strip(),
            "imageData": image_data.strip(),
            "imageName": image_name.strip(),
            "apiUrl": api_url.strip(),
            "apiMethod": api_method,
            "apiBody": api_body,
            "aiPrompt": ai_prompt,
            "encrypt": bool(body.get("encrypt", job.get("encrypt", False))),
            "mode": mode,
            "runAt": run_at,
            "windowStart": _valid_hhmm(
                str(body.get("windowStart") or job.get("windowStart") or "09:00")
            ),
            "windowEnd": _valid_hhmm(
                str(body.get("windowEnd") or job.get("windowEnd") or "18:00")
            ),
            "activeFrom": str(body.get("activeFrom") or job.get("activeFrom") or ""),
            "activeUntil": str(body.get("activeUntil") or job.get("activeUntil") or ""),
            "intervalMinutes": interval,
            "maxRuns": max_runs,
            "enabled": bool(body.get("enabled", job.get("enabled", True))),
            "running": False,
            "createdAt": job.get("createdAt") or _now_iso(),
            "updatedAt": _now_iso(),
            "sentCount": int(job.get("sentCount") or 0),
            "lastRunAt": job.get("lastRunAt"),
            "lastMessageId": job.get("lastMessageId"),
            "lastError": job.get("lastError"),
            "history": job.get("history") or [],
        }
    )
    job["nextRunAt"] = _compute_next_epoch(job)
    job["status"] = "waiting" if job["enabled"] and job["nextRunAt"] else "paused"
    return job


def _valid_hhmm(value: str) -> str:
    if not re.fullmatch(r"\d{2}:\d{2}", value or ""):
        raise WebError(HTTPStatus.BAD_REQUEST, "time windows must use HH:MM.")
    hour, minute = (int(part) for part in value.split(":", 1))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise WebError(HTTPStatus.BAD_REQUEST, "time windows must use HH:MM.")
    return f"{hour:02d}:{minute:02d}"


def _parse_local_datetime(value: str) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise WebError(HTTPStatus.BAD_REQUEST, f"invalid datetime: {value}") from exc


def _parse_date(value: str) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise WebError(HTTPStatus.BAD_REQUEST, f"invalid date: {value}") from exc


def _compute_next_epoch(job: dict[str, Any], after_epoch: float | None = None) -> float | None:
    if not job.get("enabled"):
        return None
    mode = job.get("mode")
    if mode == "once":
        dt = _parse_local_datetime(str(job.get("runAt") or ""))
        return dt.timestamp() if dt else None
    after = _dt.datetime.fromtimestamp(after_epoch or time.time())
    return _next_repeat_epoch(job, after)


def _next_repeat_epoch(job: dict[str, Any], after: _dt.datetime) -> float | None:
    start_date = _parse_date(str(job.get("activeFrom") or ""))
    until_date = _parse_date(str(job.get("activeUntil") or ""))
    window_start = _valid_hhmm(str(job.get("windowStart") or "09:00"))
    window_end = _valid_hhmm(str(job.get("windowEnd") or "18:00"))
    start_hour, start_min = (int(x) for x in window_start.split(":", 1))
    end_hour, end_min = (int(x) for x in window_end.split(":", 1))
    crosses_midnight = (end_hour, end_min) <= (start_hour, start_min)

    day = after.date()
    if crosses_midnight:
        prev_day = day - _dt.timedelta(days=1)
        if (not start_date or prev_day >= start_date) and (
            not until_date or prev_day <= until_date
        ):
            prev_start = _dt.datetime.combine(prev_day, _dt.time(start_hour, start_min))
            prev_end = _dt.datetime.combine(prev_day, _dt.time(end_hour, end_min))
            prev_end += _dt.timedelta(days=1)
            if prev_start <= after <= prev_end:
                return after.timestamp()
    if start_date and day < start_date:
        day = start_date
        after = _dt.datetime.combine(day, _dt.time(start_hour, start_min))
    for _ in range(370):
        if until_date and day > until_date:
            return None
        start = _dt.datetime.combine(day, _dt.time(start_hour, start_min))
        end = _dt.datetime.combine(day, _dt.time(end_hour, end_min))
        if end <= start:
            end += _dt.timedelta(days=1)
        if after <= start:
            return start.timestamp()
        if start <= after <= end:
            return after.timestamp()
        day = day + _dt.timedelta(days=1)
        after = _dt.datetime.combine(day, _dt.time(0, 0))
    return None


def _advance_after_success(job: dict[str, Any]) -> None:
    mode = job.get("mode")
    sent_count = int(job.get("sentCount") or 0)
    max_runs = int(job.get("maxRuns") or 0)
    if mode == "once" or (max_runs and sent_count >= max_runs):
        job["enabled"] = False
        job["nextRunAt"] = None
        job["status"] = "completed"
        return
    interval = max(1, int(job.get("intervalMinutes") or 1))
    after = _dt.datetime.now() + _dt.timedelta(minutes=interval)
    job["nextRunAt"] = _next_repeat_epoch(job, after)
    if not job["nextRunAt"]:
        job["enabled"] = False
    job["status"] = "waiting" if job["nextRunAt"] else "completed"
    if not job["nextRunAt"]:
        job["enabled"] = False


def _advance_after_error(job: dict[str, Any]) -> None:
    if job.get("mode") == "once":
        job["enabled"] = False
        job["nextRunAt"] = None
        return
    interval = max(1, int(job.get("intervalMinutes") or 1))
    after = _dt.datetime.now() + _dt.timedelta(minutes=interval)
    job["nextRunAt"] = _next_repeat_epoch(job, after)


def _append_history(job: dict[str, Any], entry: dict[str, Any]) -> None:
    history = job.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        job["history"] = history
    history.append(entry)
    del history[:-20]


BotLogFn = Callable[[str, str, bool, dict[str, Any] | None], None]


def _bot_log(
    log: BotLogFn | None,
    action: str,
    detail: str = "",
    *,
    ok: bool = True,
    data: dict[str, Any] | None = None,
) -> None:
    if log is not None:
        log(action, detail, ok, data)


_RAND_RE = re.compile(r"\{rand:(-?\d+)-(-?\d+)\}")
_DIGITS_RE = re.compile(r"\{([123])D\}")


def _apply_placeholders(text: str) -> str:
    """Substitute dynamic placeholders in a job string, fresh on every send.

    ``{1D}`` -> a random digit; ``{2D}`` / ``{3D}`` -> zero-padded random 2-/3-digit
    numbers; ``{date}`` / ``{time}`` / ``{datetime}`` -> the current local values;
    ``{rand:A-B}`` -> a random integer in ``[A, B]`` (bounds may be given either way).
    """
    if not text or "{" not in text:
        return text

    def _rand(match: re.Match[str]) -> str:
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            low, high = high, low
        return str(random.randint(low, high))

    def _digits(match: re.Match[str]) -> str:
        # a fresh random number per occurrence, zero-padded to the digit count
        width = int(match.group(1))
        return f"{random.randint(0, 10**width - 1):0{width}d}"

    text = _RAND_RE.sub(_rand, text)
    text = _DIGITS_RE.sub(_digits, text)
    if "{date" in text or "{time}" in text:
        now = _dt.datetime.now()
        text = text.replace("{datetime}", now.strftime("%d/%m/%Y %H:%M"))
        text = text.replace("{date}", now.strftime("%d/%m/%Y"))
        text = text.replace("{time}", now.strftime("%H:%M"))
    return text


def _send_job_contents(
    api: OkLine,
    to: str,
    job: dict[str, Any],
    *,
    ai_settings: dict[str, Any] | None = None,
    log: BotLogFn | None = None,
) -> list[Any]:
    items = _resolve_job_contents(job, ai_settings=ai_settings, log=log)
    if not items:
        raise WebError(HTTPStatus.BAD_REQUEST, "No content to send.")
    message_ids: list[Any] = []
    encrypt = bool(job.get("encrypt"))
    for item in items:
        kind = item.get("kind")
        try:
            if kind == "text":
                text = str(item.get("text") or "")
                if not text:
                    continue
                result = api.send_encrypted_text(to, text) if encrypt else api.send_text(to, text)
            elif kind == "image":
                if encrypt:
                    raise WebError(HTTPStatus.BAD_REQUEST, "Images cannot be sent with E2EE.")
                result = api.send_image(to, item["data"], name=item.get("name"))
            else:
                raise WebError(HTTPStatus.BAD_REQUEST, f"Unsupported content kind: {kind}")
        except Exception as exc:
            _bot_log(
                log,
                "send.item.error",
                str(exc),
                ok=False,
                data={"kind": kind, "to": to},
            )
            raise
        message_ids.append(result.get("id") if isinstance(result, dict) else result)
        _bot_log(
            log,
            "send.item.success",
            str(kind or ""),
            data={"kind": kind, "to": to, "encrypted": encrypt},
        )
    if not message_ids:
        raise WebError(HTTPStatus.BAD_REQUEST, "No content to send.")
    return message_ids


def _resolve_job_contents(
    job: dict[str, Any],
    *,
    ai_settings: dict[str, Any] | None = None,
    log: BotLogFn | None = None,
) -> list[dict[str, Any]]:
    source = str(job.get("contentSource") or "text").lower()
    if source == "text":
        texts = [str(x) for x in (job.get("patternTexts") or []) if str(x).strip()]
        base = random.choice(texts) if texts else str(job.get("text") or "")
        text = _apply_placeholders(base)
        if not text:
            raise WebError(HTTPStatus.BAD_REQUEST, "Schedule text is required.")
        _bot_log(log, "content.text", _short_text(text, 120), data={"patterns": bool(texts)})
        return [{"kind": "text", "text": text}]
    if source == "image":
        image_data = job.get("imageData")
        if image_data:
            try:
                raw = base64.b64decode(str(image_data))
            except ValueError as exc:
                raise WebError(HTTPStatus.BAD_REQUEST, "Job image is invalid.") from exc
            _bot_log(
                log,
                "content.image.uploaded",
                str(job.get("imageName") or "image.jpg"),
                data={"bytes": len(raw)},
            )
            return [
                {"kind": "image", "data": raw, "name": job.get("imageName") or "image.jpg"}
            ]
        return [_image_content(_apply_placeholders(str(job.get("imageSource") or "")), log=log)]
    if source == "api":
        return _api_contents(job, log=log)
    if source == "ai_image":
        cfg = ai_settings or {}
        # Prompt patterns work like the text source: tick 2+ and each send picks
        # one at random; otherwise fall back to the single AI prompt field.
        texts = [str(x) for x in (job.get("patternTexts") or []) if str(x).strip()]
        base = random.choice(texts) if texts else str(job.get("aiPrompt") or job.get("text") or "")
        prompt = _prepare_ai_image_prompt(_apply_placeholders(base))
        provider = str(cfg.get("provider") or "google")
        model = str(cfg.get("model") or "")
        aspect_ratio = str(cfg.get("aspectRatio") or "")
        _bot_log(
            log,
            "content.ai.start",
            prompt,
            data={
                "provider": provider,
                "model": model,
                "aspectRatio": aspect_ratio,
                "prompt": prompt,
            },
        )
        try:
            result = _generate_ai_image(
                prompt,
                provider=provider,
                api_key=str(cfg.get("apiKey") or ""),
                model=model,
                aspect_ratio=aspect_ratio,
                base_url=str(cfg.get("baseUrl") or ""),
                log=log,
            )
        except Exception as exc:
            _bot_log(
                log,
                "content.ai.error",
                str(exc),
                ok=False,
                data={"provider": provider, "model": model, "aspectRatio": aspect_ratio},
            )
            raise
        _bot_log(
            log,
            "content.ai.success",
            result.get("name") or "image",
            data={
                "bytes": len(result.get("data") or b""),
                "provider": str(cfg.get("provider") or "google"),
            },
        )
        return [{"kind": "image", "data": result["data"], "name": result["name"]}]
    raise WebError(HTTPStatus.BAD_REQUEST, f"Unsupported content source: {source}")


def _api_contents(job: dict[str, Any], *, log: BotLogFn | None = None) -> list[dict[str, Any]]:
    url = _apply_placeholders(str(job.get("apiUrl") or "").strip())
    if not url:
        raise WebError(HTTPStatus.BAD_REQUEST, "API URL is required.")
    method = str(job.get("apiMethod") or "GET").upper()
    if method not in {"GET", "POST"}:
        raise WebError(HTTPStatus.BAD_REQUEST, "apiMethod must be GET or POST.")
    body = _apply_placeholders(str(job.get("apiBody") or "").strip())
    kwargs: dict[str, Any] = {"timeout": 25}
    if method == "POST" and body:
        try:
            kwargs["json"] = json.loads(body)
        except ValueError:
            kwargs["data"] = body.encode("utf-8")
            kwargs["headers"] = {"content-type": "text/plain; charset=utf-8"}
    _bot_log(log, "content.api.fetch", _safe_url(url), data={"method": method})
    try:
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
    except requests.RequestException as exc:
        _bot_log(log, "content.api.error", str(exc), ok=False, data={"method": method})
        raise WebError(HTTPStatus.BAD_GATEWAY, f"API request failed: {exc}") from exc

    ctype = resp.headers.get("content-type", "").lower()
    if ctype.startswith("image/"):
        items = [{"kind": "image", "data": resp.content, "name": _name_from_url(url)}]
        _bot_log(log, "content.api.loaded", ctype, data={"items": 1, "bytes": len(resp.content)})
        return items
    if "json" in ctype or resp.text.lstrip()[:1] in "[{":
        try:
            items = _contents_from_api_json(resp.json(), base_url=url, log=log)
            _bot_log(
                log,
                "content.api.loaded",
                ctype or "json",
                data={"items": len(items), "kinds": [item.get("kind") for item in items]},
            )
            return items
        except ValueError as exc:
            raise WebError(
                HTTPStatus.BAD_GATEWAY, f"API returned invalid JSON: {exc}"
            ) from exc
    text = resp.text.strip()
    if not text:
        raise WebError(HTTPStatus.BAD_GATEWAY, "API returned no usable content.")
    _bot_log(log, "content.api.loaded", "text", data={"items": 1})
    return [{"kind": "text", "text": text}]


def _contents_from_api_json(
    data: Any, *, base_url: str = "", log: BotLogFn | None = None
) -> list[dict[str, Any]]:
    if isinstance(data, list):
        if not data:
            raise WebError(HTTPStatus.BAD_GATEWAY, "API returned an empty list.")
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        payload = data["data"]
    else:
        payload = data
    if not isinstance(payload, dict):
        if isinstance(payload, str) and payload.strip():
            return [{"kind": "text", "text": payload.strip()}]
        raise WebError(HTTPStatus.BAD_GATEWAY, "API JSON must be an object.")

    items: list[dict[str, Any]] = []
    image_b64 = _first_str(payload, ("imageBase64", "image_base64", "base64Image"))
    image_url = _first_str(payload, ("imageUrl", "image_url", "image", "url"))
    text = _first_str(payload, ("text", "message", "content", "body"))

    if image_b64:
        try:
            items.append(
                {
                    "kind": "image",
                    "data": base64.b64decode(image_b64),
                    "name": "api-image.jpg",
                }
            )
        except ValueError as exc:
            raise WebError(HTTPStatus.BAD_GATEWAY, "API imageBase64 is invalid.") from exc
    elif image_url:
        items.append(_image_content(image_url, base_url=base_url, log=log))

    if text:
        items.append({"kind": "text", "text": text})

    if not items:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            "API JSON must include text/message/content or imageUrl/image_base64.",
        )
    return items


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _image_content(
    source: str, *, base_url: str = "", log: BotLogFn | None = None
) -> dict[str, Any]:
    source = source.strip()
    if not source:
        raise WebError(HTTPStatus.BAD_REQUEST, "Image source is required.")
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        _bot_log(log, "content.image.fetch", _safe_url(source))
        try:
            data, name = _download_image(source)
        except Exception as exc:
            _bot_log(log, "content.image.error", str(exc), ok=False)
            raise
        _bot_log(log, "content.image.loaded", name, data={"bytes": len(data)})
        return {"kind": "image", "data": data, "name": name}
    if base_url and not parsed.scheme:
        absolute = urljoin(base_url, source)
        _bot_log(log, "content.image.fetch", _safe_url(absolute))
        try:
            data, name = _download_image(absolute)
        except Exception as exc:
            _bot_log(log, "content.image.error", str(exc), ok=False)
            raise
        _bot_log(log, "content.image.loaded", name, data={"bytes": len(data)})
        return {"kind": "image", "data": data, "name": name}
    return {"kind": "image", "data": source, "name": os.path.basename(source)}


def _download_image(url: str) -> tuple[bytes, str]:
    try:
        resp = requests.get(url, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise WebError(HTTPStatus.BAD_GATEWAY, f"Image download failed: {exc}") from exc
    ctype = resp.headers.get("content-type", "").lower()
    if ctype and not ctype.startswith("image/"):
        raise WebError(HTTPStatus.BAD_GATEWAY, f"Image URL returned {ctype or 'non-image'}")
    return resp.content, _name_from_url(url)


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return _short_text(url, 180)
    if parsed.netloc.endswith("line-apps.com"):
        return f"{parsed.scheme}://{parsed.netloc}/..."
    path = parsed.path
    if len(path) > 64:
        parts = [part for part in path.split("/") if part]
        path = "/" + "/".join(parts[:2]) + "/..." if parts else ""
    suffix = "?..." if parsed.query else ""
    return _short_text(f"{parsed.scheme}://{parsed.netloc}{path}{suffix}", 180)


def _name_from_url(url: str) -> str:
    name = os.path.basename(urlparse(url).path)
    return name or "image.jpg"


# --- AI image providers -----------------------------------------------------
# Three providers can render an image from a text prompt:
#  * "google"        — Google's Gemini image model ("Nano Banana") called
#                      directly on generativelanguage.googleapis.com; the image
#                      comes back base64 inside candidates[].content.parts[].
#  * "nanobananaapi" — the hosted nanobananaapi.ai service, an async task API
#                      (create -> poll record-info -> download resultImageUrl).
#  * "fal"           - fal.ai Queue API with model-specific payload adapters.
# Contracts verified against official provider documentation (Jul 2026).
NANO_BANANA_MODEL = "gemini-2.5-flash-image"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
NBAPI_BASE_URL = "https://api.nanobananaapi.ai"
FAL_BASE_URL = "https://queue.fal.run"
# callBackUrl is a required field even when we poll for the result; this inert
# placeholder satisfies validation and simply never receives the webhook.
NBAPI_CALLBACK_URL = "https://okline.invalid/nano-callback"
NBAPI_DEFAULT_MODEL = "nano-banana"
NBAPI_DEFAULT_ASPECT_RATIO = "auto"
NBAPI_MODELS = ("nano-banana", "nano-banana-2", "nano-banana-pro")
NBAPI_MODEL_LABELS = {
    "nano-banana": "NanoBanana Classic",
    "nano-banana-2": "NanoBanana 2",
    "nano-banana-pro": "NanoBanana Pro",
}

DEFAULT_PATTERN_CATEGORY_ID = "general"
NBAPI_ASPECT_RATIOS = (
    "auto",
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)
FAL_DEFAULT_MODEL = "fal-ai/flux/schnell"
FAL_MODELS = (
    "fal-ai/flux/schnell",
    "fal-ai/flux/dev",
    "fal-ai/flux-2/turbo",
    "fal-ai/flux-2",
    "fal-ai/nano-banana-2",
    "ideogram/v4",
    "bytedance/seedream/v5/lite/text-to-image",
    "fal-ai/qwen-image-2/text-to-image",
    "fal-ai/qwen-image-2/pro/text-to-image",
    "openai/gpt-image-2",
    "fal-ai/stable-diffusion-v35-large",
)
FAL_MODEL_LABELS = {
    "fal-ai/flux/schnell": "FLUX.1 Schnell (fast)",
    "fal-ai/flux/dev": "FLUX.1 Dev (quality)",
    "fal-ai/flux-2/turbo": "FLUX.2 Turbo",
    "fal-ai/flux-2": "FLUX.2 Dev",
    "fal-ai/nano-banana-2": "Nano Banana 2",
    "ideogram/v4": "Ideogram V4",
    "bytedance/seedream/v5/lite/text-to-image": "Seedream 5 Lite",
    "fal-ai/qwen-image-2/text-to-image": "Qwen Image 2 Standard",
    "fal-ai/qwen-image-2/pro/text-to-image": "Qwen Image 2 Pro",
    "openai/gpt-image-2": "GPT Image 2 (Medium)",
    "fal-ai/stable-diffusion-v35-large": "Stable Diffusion 3.5 Large",
}
FAL_MODEL_PRICES = {
    "fal-ai/flux/schnell": "$0.003/MP",
    "fal-ai/flux/dev": "$0.025/MP",
    "fal-ai/flux-2/turbo": "$0.008/MP",
    "fal-ai/flux-2": "$0.012/MP",
    "fal-ai/nano-banana-2": "$0.08/image (1K)",
    "ideogram/v4": "$0.015/MP (Balanced)",
    "bytedance/seedream/v5/lite/text-to-image": "$0.035/image",
    "fal-ai/qwen-image-2/text-to-image": "$0.035/image",
    "fal-ai/qwen-image-2/pro/text-to-image": "$0.075/image",
    "openai/gpt-image-2": "$0.04-$0.06/image (Medium ~1K)",
    "fal-ai/stable-diffusion-v35-large": "$0.065/MP",
}
FAL_DEFAULT_IMAGE_SIZE = "landscape_4_3"
FAL_IMAGE_SIZES = (
    "square_hd",
    "square",
    "portrait_4_3",
    "portrait_16_9",
    "landscape_4_3",
    "landscape_16_9",
)
FAL_IMAGE_SIZE_LABELS = {
    "square_hd": "Square HD",
    "square": "Square",
    "portrait_4_3": "Portrait 4:3",
    "portrait_16_9": "Portrait 9:16",
    "landscape_4_3": "Landscape 4:3",
    "landscape_16_9": "Landscape 16:9",
}
FAL_SIZE_TO_ASPECT_RATIO = {
    "square_hd": "1:1",
    "square": "1:1",
    "portrait_4_3": "3:4",
    "portrait_16_9": "9:16",
    "landscape_4_3": "4:3",
    "landscape_16_9": "16:9",
}
AI_IMAGE_PROMPT_INSTRUCTION = (
    "Create an image based on the user prompt below and return an image as the output. "
    "If the prompt contains text or numbers intended to appear in the image, render "
    "them clearly and preserve them exactly without translation, omission, or alteration."
)
AI_PROVIDERS = ("google", "nanobananaapi", "fal")
AI_PROVIDER_META: dict[str, dict[str, Any]] = {
    "google": {
        "label": "Google Gemini (direct)",
        "baseUrl": GEMINI_BASE_URL,
        "model": NANO_BANANA_MODEL,
        "models": ("gemini-2.5-flash-image", "gemini-2.5-flash-image-preview"),
        "modelLabel": "Image model",
    },
    "nanobananaapi": {
        "label": "nanobananaapi.ai (hosted)",
        "baseUrl": NBAPI_BASE_URL,
        "model": NBAPI_DEFAULT_MODEL,
        "models": NBAPI_MODELS,
        "modelLabels": NBAPI_MODEL_LABELS,
        "aspectRatio": NBAPI_DEFAULT_ASPECT_RATIO,
        "aspectRatios": NBAPI_ASPECT_RATIOS,
        "modelLabel": "Image model",
    },
    "fal": {
        "label": "fal.ai (Queue API)",
        "baseUrl": FAL_BASE_URL,
        "model": FAL_DEFAULT_MODEL,
        "models": FAL_MODELS,
        "modelLabels": FAL_MODEL_LABELS,
        "modelPrices": FAL_MODEL_PRICES,
        "aspectRatio": FAL_DEFAULT_IMAGE_SIZE,
        "aspectRatios": FAL_IMAGE_SIZES,
        "aspectRatioLabels": FAL_IMAGE_SIZE_LABELS,
        "modelLabel": "Image model",
    },
}
# Back-compat alias (the Google model list).
AI_IMAGE_MODELS = AI_PROVIDER_META["google"]["models"]


def _ai_provider_meta(provider: str) -> dict[str, Any]:
    return AI_PROVIDER_META.get(provider) or AI_PROVIDER_META["google"]


def _mask_secret(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "••••" + value[-4:]


def _ai_image_ext(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
        base, ".png"
    )


def _extract_inline_image(data: Any) -> tuple[bytes, str] | None:
    """Pull the first base64 image part out of a generateContent response.

    ``parts`` is an array that may hold a text part and an image part in any
    order, so we scan for whichever object carries an ``inlineData`` blob rather
    than assuming index 0.  REST responses are camelCase; snake_case is tolerated
    defensively.
    """
    candidates = data.get("candidates") if isinstance(data, dict) else None
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict) or not inline.get("data"):
                continue
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
            try:
                raw = base64.b64decode(inline["data"])
            except (ValueError, TypeError):
                continue
            if raw:
                return raw, mime
    return None


def _ai_refusal_text(data: Any) -> str:
    for cand in (data.get("candidates") or []) if isinstance(data, dict) else []:
        if not isinstance(cand, dict):
            continue
        parts = (cand.get("content") or {}).get("parts") or []
        for part in parts:
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"]).strip()
    return ""


def _gemini_http_error(resp: requests.Response) -> str:
    message = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or "")
    except ValueError:
        message = (resp.text or "").strip()
    message = " ".join(message.split())  # collapse the multi-line quota blurb
    if len(message) > 240:
        message = message[:240] + "…"
    if not message:
        if resp.status_code == 429:
            message = "rate limit or quota exceeded"
        elif resp.status_code in (401, 403):
            message = "the API key was rejected"
    tail = f": {message}" if message else ""
    return f"Google Gemini error {resp.status_code}{tail}"


def _ai_error_code(status: int) -> str:
    # Distinct codes so the browser shows an AI-specific message instead
    # of colliding with the generic LINE "upstream_error" i18n string.
    if status == 429:
        return "ai_quota"
    if status in (401, 403):
        return "ai_key_rejected"
    return "ai_error"


def _generate_ai_image(
    prompt: str,
    *,
    provider: str = "google",
    api_key: str,
    model: str = "",
    aspect_ratio: str = "",
    base_url: str = "",
    log: BotLogFn | None = None,
) -> dict[str, Any]:
    """Render ``prompt`` with the chosen provider and return the image bytes.

    Returns a dict ``{"data": bytes, "mime": str, "name": str}``.
    """
    prompt = _prepare_ai_image_prompt(prompt)
    provider = (provider or "google").strip().lower()
    if provider == "nanobananaapi":
        return _generate_nbapi_image(
            prompt,
            api_key=api_key,
            model=model,
            aspect_ratio=aspect_ratio,
            base_url=base_url or NBAPI_BASE_URL,
            log=log,
        )
    if provider == "fal":
        return _generate_fal_image(
            prompt,
            api_key=api_key,
            model=model,
            image_size=aspect_ratio,
            base_url=base_url or FAL_BASE_URL,
            log=log,
        )
    return _generate_gemini_image(
        prompt,
        api_key=api_key,
        model=model or NANO_BANANA_MODEL,
        base_url=base_url or GEMINI_BASE_URL,
        log=log,
    )


def _generate_gemini_image(
    prompt: str,
    *,
    api_key: str,
    model: str = NANO_BANANA_MODEL,
    base_url: str = GEMINI_BASE_URL,
    log: BotLogFn | None = None,
) -> dict[str, Any]:
    """Ask Google's Gemini image model to render ``prompt``."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise WebError(HTTPStatus.BAD_REQUEST, "AI image prompt is required.")
    if not (api_key or "").strip():
        raise WebError(
            HTTPStatus.BAD_REQUEST,
            "No Google Gemini API key. Add one in the AI Settings tab.",
            "ai_not_configured",
        )
    model = (model or NANO_BANANA_MODEL).strip() or NANO_BANANA_MODEL
    base = (base_url or GEMINI_BASE_URL).strip().rstrip("/") or GEMINI_BASE_URL
    url = f"{base}/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    _bot_log(
        log,
        "content.ai.request",
        f"Google Gemini · {model}",
        data={"provider": "google", "model": model, "endpoint": url, "prompt": prompt},
    )
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "x-goog-api-key": api_key.strip(),
                "content-type": "application/json",
            },
            timeout=120,
        )
    except requests.RequestException as exc:
        raise WebError(
            HTTPStatus.BAD_GATEWAY, f"Google Gemini request failed: {exc}", "ai_error"
        ) from exc
    if resp.status_code != 200:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            _gemini_http_error(resp),
            _ai_error_code(resp.status_code),
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            "Google Gemini returned a non-JSON response.",
            "ai_error",
        ) from exc
    image = _extract_inline_image(data)
    if image is None:
        refusal = _ai_refusal_text(data)
        detail = f" Model said: {refusal}" if refusal else ""
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            f"Google Gemini returned no image for that prompt.{detail}",
            "ai_error",
        )
    raw, mime = image
    return {"data": raw, "mime": mime, "name": f"gemini-image{_ai_image_ext(mime)}"}


def _fal_http_error(resp: requests.Response) -> str:
    message: Any = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            message = body.get("detail") or body.get("error") or body.get("message") or ""
    except ValueError:
        message = (resp.text or "").strip()
    structured_detail = isinstance(message, list)
    if structured_detail:
        details = []
        for item in message:
            if not isinstance(item, dict):
                details.append(str(item))
                continue
            text = str(item.get("msg") or item.get("message") or "").strip()
            error_type = str(item.get("type") or "").strip()
            docs_url = str(item.get("url") or "").strip()
            suffix = " ".join(
                part for part in (f"[{error_type}]" if error_type else "", docs_url) if part
            )
            details.append(" ".join(part for part in (text, suffix) if part))
        message = "; ".join(part for part in details if part)
    elif isinstance(message, dict):
        message = json.dumps(message, ensure_ascii=False, default=_json_default)
    message = " ".join(str(message or "").split())
    if len(message) > 240 and not structured_detail:
        message = message[:240] + "…"
    return f"fal.ai error {resp.status_code}" + (f": {message}" if message else "")


def _fal_error_code(status: int) -> str:
    if status in (401, 403):
        return "ai_key_rejected"
    if status in (402, 429):
        return "fal_quota"
    return "ai_error"


def _prepare_ai_image_prompt(value: Any) -> str:
    prompt = str(value or "").strip()
    if not prompt or prompt.startswith(AI_IMAGE_PROMPT_INSTRUCTION):
        return prompt
    return f"{AI_IMAGE_PROMPT_INSTRUCTION}\n\nUser prompt:\n{prompt}"


def _fal_json(resp: requests.Response, operation: str) -> dict[str, Any]:
    if resp.status_code < 200 or resp.status_code >= 300:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            _fal_http_error(resp),
            _fal_error_code(resp.status_code),
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            f"fal.ai returned a non-JSON response while {operation}.",
            "ai_error",
        ) from exc
    if not isinstance(data, dict):
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            f"fal.ai returned an invalid response while {operation}.",
            "ai_error",
        )
    return data


def _fal_model_payload(prompt: str, model: str, image_size: str) -> dict[str, Any]:
    """Build the provider-specific input while keeping one shared size control."""
    if model == "fal-ai/nano-banana-2":
        return {
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": FAL_SIZE_TO_ASPECT_RATIO[image_size],
            "output_format": "png",
            "resolution": "1K",
            "limit_generations": True,
            "enable_web_search": False,
        }
    if model == "openai/gpt-image-2":
        # GPT Image 2 requires at least 655,360 pixels, so its 1:1 option must
        # use the 1024px preset rather than fal's generic 512px square.
        if image_size == "square":
            image_size = "square_hd"
        return {
            "prompt": prompt,
            "image_size": image_size,
            "quality": "medium",
            "num_images": 1,
            "output_format": "png",
        }
    if model == "ideogram/v4":
        return {
            "prompt": prompt,
            "image_size": image_size,
            "expansion_model": "Medium",
            "rendering_speed": "BALANCED",
            "num_images": 1,
            "enable_safety_checker": True,
            "output_format": "png",
        }
    if model == "bytedance/seedream/v5/lite/text-to-image":
        return {
            "prompt": prompt,
            "image_size": image_size,
            "num_images": 1,
            "max_images": 1,
            "enable_safety_checker": True,
            "output_format": "png",
        }
    payload = {
        "prompt": prompt,
        "image_size": image_size,
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "png",
    }
    if model.startswith("fal-ai/qwen-image-2/"):
        payload["enable_prompt_expansion"] = True
    return payload


def _generate_fal_image(
    prompt: str,
    *,
    api_key: str,
    model: str = FAL_DEFAULT_MODEL,
    image_size: str = FAL_DEFAULT_IMAGE_SIZE,
    base_url: str = FAL_BASE_URL,
    log: BotLogFn | None = None,
) -> dict[str, Any]:
    """Render an image through fal.ai's durable Queue API."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise WebError(HTTPStatus.BAD_REQUEST, "AI image prompt is required.")
    if not (api_key or "").strip():
        raise WebError(
            HTTPStatus.BAD_REQUEST,
            "No fal.ai API key. Add one in the AI Settings tab.",
            "ai_not_configured",
        )
    model = (model or FAL_DEFAULT_MODEL).strip()
    if model not in FAL_MODELS:
        model = FAL_DEFAULT_MODEL
    image_size = (image_size or FAL_DEFAULT_IMAGE_SIZE).strip()
    if image_size not in FAL_IMAGE_SIZES:
        image_size = FAL_DEFAULT_IMAGE_SIZE
    base = (base_url or FAL_BASE_URL).strip().rstrip("/") or FAL_BASE_URL
    endpoint = f"{base}/{model}"
    headers = {
        "Authorization": f"Key {api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload = _fal_model_payload(prompt, model, image_size)
    _bot_log(
        log,
        "content.ai.request",
        f"fal.ai · {model}",
        data={
            "provider": "fal",
            "model": model,
            "price": FAL_MODEL_PRICES.get(model, ""),
            "imageSize": image_size,
            "endpoint": endpoint,
            "prompt": prompt,
        },
    )
    try:
        submit_resp = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            f"fal.ai queue request failed: {exc}",
            "ai_error",
        ) from exc
    submitted = _fal_json(submit_resp, "submitting the request")
    request_id = str(submitted.get("request_id") or "").strip()
    if not request_id:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            "fal.ai did not return a request id.",
            "ai_error",
        )
    status_url = str(submitted.get("status_url") or "").strip()
    if not status_url:
        status_url = f"{endpoint}/requests/{request_id}/status"
    response_url = str(submitted.get("response_url") or "").strip()
    if not response_url:
        response_url = f"{endpoint}/requests/{request_id}"
    _bot_log(
        log,
        "content.ai.task",
        request_id,
        data={"provider": "fal", "model": model, "taskId": request_id},
    )

    completed = False
    for attempt in range(1, 121):
        if attempt > 1:
            time.sleep(2.0)
        try:
            status_resp = requests.get(
                status_url,
                params={"logs": 1},
                headers={"Authorization": headers["Authorization"]},
                timeout=30,
            )
        except requests.RequestException as exc:
            if attempt < 4:
                _bot_log(
                    log,
                    "content.ai.poll",
                    f"attempt {attempt}: retrying ({_short_text(exc, 100)})",
                    data={"provider": "fal", "taskId": request_id, "attempt": attempt},
                )
                continue
            raise WebError(
                HTTPStatus.BAD_GATEWAY,
                f"fal.ai status request failed: {exc}",
                "ai_error",
            ) from exc
        status_data = _fal_json(status_resp, "checking request status")
        status = str(status_data.get("status") or "").upper()
        _bot_log(
            log,
            "content.ai.poll",
            f"attempt {attempt}: {status.lower() or 'waiting'}",
            data={
                "provider": "fal",
                "model": model,
                "taskId": request_id,
                "attempt": attempt,
                "status": status,
                "queuePosition": status_data.get("queue_position"),
            },
        )
        if status == "COMPLETED":
            if status_data.get("error"):
                raise WebError(
                    HTTPStatus.BAD_GATEWAY,
                    f"fal.ai generation failed: {status_data['error']}",
                    "ai_error",
                )
            completed = True
            break
        if status in {"FAILED", "CANCELLED", "CANCELED"}:
            detail = status_data.get("error") or status_data.get("detail") or status.lower()
            raise WebError(
                HTTPStatus.BAD_GATEWAY,
                f"fal.ai generation failed: {detail}",
                "ai_error",
            )
    if not completed:
        raise WebError(
            HTTPStatus.GATEWAY_TIMEOUT,
            "fal.ai timed out before the image was ready.",
            "ai_error",
        )

    try:
        result_resp = requests.get(
            response_url,
            headers={"Authorization": headers["Authorization"]},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            f"fal.ai result request failed: {exc}",
            "ai_error",
        ) from exc
    result = _fal_json(result_resp, "retrieving the result")
    output = result.get("data") if isinstance(result.get("data"), dict) else result
    images = output.get("images") if isinstance(output, dict) else None
    first = images[0] if isinstance(images, list) and images else None
    image_url = str(first.get("url") or "") if isinstance(first, dict) else ""
    if not image_url:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            "fal.ai completed the request but returned no image URL.",
            "ai_error",
        )
    _bot_log(
        log,
        "content.ai.download",
        _safe_url(image_url),
        data={"provider": "fal", "model": model, "taskId": request_id},
    )
    raw, name = _download_image(image_url)
    mime = _sniff_content_type(raw)
    return {
        "data": raw,
        "mime": mime if mime.startswith("image/") else "image/png",
        "name": name or f"fal-image{_ai_image_ext(mime)}",
    }


def _nbapi_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    # the base endpoints use "msg", the newer ones "message"
    return str(data.get("msg") or data.get("message") or "").strip()


def _generate_nbapi_image(
    prompt: str,
    *,
    api_key: str,
    model: str = "",
    aspect_ratio: str = "",
    base_url: str = NBAPI_BASE_URL,
    log: BotLogFn | None = None,
) -> dict[str, Any]:
    """Render ``prompt`` via the hosted nanobananaapi.ai async task API.

    Submits a generation task, polls ``record-info`` until it finishes, then
    downloads the resulting image URL.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise WebError(HTTPStatus.BAD_REQUEST, "AI image prompt is required.")
    if not (api_key or "").strip():
        raise WebError(
            HTTPStatus.BAD_REQUEST,
            "No nanobananaapi.ai API key. Add one in the AI Settings tab.",
            "ai_not_configured",
        )
    base = (base_url or NBAPI_BASE_URL).strip().rstrip("/") or NBAPI_BASE_URL
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "content-type": "application/json",
    }
    # "TEXTTOIAMGE" is the vendor's (misspelled) enum value — send it verbatim.
    model = (model or NBAPI_DEFAULT_MODEL).strip()
    if model in NBAPI_ASPECT_RATIOS:
        aspect_ratio = aspect_ratio or model
        model = NBAPI_DEFAULT_MODEL
    if model not in NBAPI_MODELS:
        model = NBAPI_DEFAULT_MODEL
    aspect_ratio = (aspect_ratio or NBAPI_DEFAULT_ASPECT_RATIO).strip()
    if aspect_ratio not in NBAPI_ASPECT_RATIOS:
        aspect_ratio = NBAPI_DEFAULT_ASPECT_RATIO
    endpoint = {
        "nano-banana": "/api/v1/nanobanana/generate",
        "nano-banana-2": "/api/v1/nanobanana/generate-2",
        "nano-banana-pro": "/api/v1/nanobanana/generate-pro",
    }[model]
    if model == "nano-banana":
        body: dict[str, Any] = {
            "prompt": prompt,
            "type": "TEXTTOIAMGE",
            "numImages": 1,
            "callBackUrl": NBAPI_CALLBACK_URL,
        }
        if aspect_ratio != "auto":
            body["image_size"] = aspect_ratio
    else:
        body = {
            "prompt": prompt,
            "imageUrls": [],
            "aspectRatio": aspect_ratio,
            "resolution": "2K" if model == "nano-banana-pro" else "1K",
            "callBackUrl": NBAPI_CALLBACK_URL,
        }
        if model == "nano-banana-2":
            body["googleSearch"] = False
            body["outputFormat"] = "jpg"

    task_id = _nbapi_create_task(
        base,
        headers,
        body,
        endpoint=endpoint,
        model=model,
        aspect_ratio=aspect_ratio,
        log=log,
    )
    result_url = _nbapi_poll_result(base, headers, task_id, log=log)
    _bot_log(
        log,
        "content.ai.download",
        _safe_url(result_url),
        data={
            "provider": "nanobananaapi",
            "taskId": task_id,
            "model": model,
            "aspectRatio": aspect_ratio,
        },
    )
    raw, name = _download_image(result_url)
    mime = _sniff_content_type(raw)
    ext = _ai_image_ext(mime) if mime.startswith("image/") else ".jpg"
    return {"data": raw, "mime": mime, "name": name or f"nano-banana{ext}"}


def _nbapi_create_task(
    base: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    endpoint: str,
    model: str,
    aspect_ratio: str,
    log: BotLogFn | None = None,
) -> str:
    _bot_log(
        log,
        "content.ai.request",
        f"nanobananaapi.ai - {model}",
        data={
            "provider": "nanobananaapi",
            "model": model,
            "aspectRatio": aspect_ratio,
            "endpoint": endpoint,
            "prompt": str(body.get("prompt") or ""),
        },
    )
    try:
        resp = requests.post(
            f"{base}{endpoint}", json=body, headers=headers, timeout=30
        )
    except requests.RequestException as exc:
        raise WebError(
            HTTPStatus.BAD_GATEWAY, f"nanobananaapi.ai request failed: {exc}", "ai_error"
        ) from exc
    try:
        data = resp.json()
    except ValueError:
        data = None
    code = data.get("code") if isinstance(data, dict) else None
    effective = (
        int(code) if code is not None and str(code).isdigit() else resp.status_code
    )
    if effective != 200 or not isinstance(data, dict):
        message = _nbapi_message(data) or (resp.text or "").strip()[:240]
        tail = f": {message}" if message else ""
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            f"nanobananaapi.ai error {effective}{tail}",
            _ai_error_code(effective),
        )
    inner = data.get("data")
    task_id = inner.get("taskId") if isinstance(inner, dict) else None
    if not task_id:
        raise WebError(
            HTTPStatus.BAD_GATEWAY,
            _nbapi_message(data) or "nanobananaapi.ai did not return a task id.",
            "ai_error",
        )
    task_id = str(task_id)
    _bot_log(
        log,
        "content.ai.task",
        _short_text(task_id, 80),
        data={"provider": "nanobananaapi", "taskId": task_id},
    )
    return task_id


def _nbapi_poll_result(
    base: str,
    headers: dict[str, str],
    task_id: str,
    *,
    timeout: float = 150.0,
    log: BotLogFn | None = None,
) -> str:
    """Poll record-info until the task succeeds; return the result image URL."""
    deadline = time.time() + timeout
    url = f"{base}/api/v1/nanobanana/record-info"
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        time.sleep(3)
        try:
            resp = requests.get(
                url, params={"taskId": task_id}, headers=headers, timeout=30
            )
            data = resp.json()
        except (requests.RequestException, ValueError):
            _bot_log(
                log,
                "content.ai.poll",
                f"attempt {attempt}: retrying",
                data={"provider": "nanobananaapi", "taskId": task_id, "attempt": attempt},
            )
            continue
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            _bot_log(
                log,
                "content.ai.poll",
                f"attempt {attempt}: waiting",
                data={"provider": "nanobananaapi", "taskId": task_id, "attempt": attempt},
            )
            continue
        flag = inner.get("successFlag")
        status = "ready" if flag in (1, "1") else "failed" if flag in (2, 3, "2", "3") else "waiting"
        _bot_log(
            log,
            "content.ai.poll",
            f"attempt {attempt}: {status}",
            data={
                "provider": "nanobananaapi",
                "taskId": task_id,
                "attempt": attempt,
                "successFlag": flag,
            },
        )
        if flag in (1, "1"):
            response = inner.get("response")
            result_url = (
                str(response.get("resultImageUrl") or "")
                if isinstance(response, dict)
                else ""
            )
            if not result_url:
                raise WebError(
                    HTTPStatus.BAD_GATEWAY,
                    "nanobananaapi.ai reported success but returned no image URL.",
                    "ai_error",
                )
            return result_url
        if flag in (2, 3, "2", "3"):
            message = str(inner.get("errorMessage") or "").strip() or "generation failed"
            raise WebError(
                HTTPStatus.BAD_GATEWAY, f"nanobananaapi.ai failed: {message}", "ai_error"
            )
        # flag 0 / unknown -> still generating; keep polling.
    raise WebError(
        HTTPStatus.GATEWAY_TIMEOUT,
        "nanobananaapi.ai timed out before the image was ready.",
        "ai_error",
    )


def _qr_svg(url: str) -> str | None:
    try:
        import qrcode
        import qrcode.image.svg
    except ModuleNotFoundError:
        return None
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def _coerce_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _message_box_id(box: dict[str, Any]) -> str:
    for key in ("id", "chatId", "chatMid", "messageBoxId", "mid"):
        value = box.get(key)
        if value:
            return str(value)
    nested = box.get("messageBox")
    if isinstance(nested, dict):
        return _message_box_id(nested)
    return ""


def _message_time(message: Any) -> int:
    if not isinstance(message, dict):
        return 0
    for key in ("createdTime", "deliveredTime", "updatedTime", "sentTime"):
        timestamp = _coerce_int(message.get(key))
        if timestamp:
            return timestamp
    return 0


def _message_box_time(box: dict[str, Any]) -> int:
    for key in ("updatedTime", "lastMessageTime", "lastMessageCreatedTime", "createdTime"):
        timestamp = _coerce_int(box.get(key))
        if timestamp:
            return timestamp
    messages = box.get("lastMessages")
    if isinstance(messages, list):
        return max((_message_time(message) for message in messages), default=0)
    nested = box.get("messageBox")
    if isinstance(nested, dict):
        return _message_box_time(nested)
    return 0


def _chat_recency_map(api: OkLine, limit: int = 1000) -> dict[str, tuple[int, int]]:
    try:
        boxes = api.get_message_boxes(limit=limit, last_messages_per_box=1)
    except TypeError:
        try:
            boxes = api.get_message_boxes(limit=limit)
        except Exception:
            return {}
    except Exception:
        return {}
    message_boxes = boxes.get("messageBoxes", []) if isinstance(boxes, dict) else []
    recency: dict[str, tuple[int, int]] = {}
    for rank, box in enumerate(message_boxes):
        if not isinstance(box, dict):
            continue
        mid = _message_box_id(box)
        if mid and mid not in recency:
            recency[mid] = (rank, _message_box_time(box))
    return recency


def _recent_chat_sort_key(row: dict[str, Any], recency: dict[str, tuple[int, int]]):
    mid = str(row.get("mid") or "")
    rank, last_at = recency.get(mid, (1_000_000_000, 0))
    name = str(row.get("name") or "").casefold()
    return (rank == 1_000_000_000, rank, -last_at, name, mid)


def _contact_rows(api: OkLine) -> list[tuple[str, dict[str, Any]]]:
    ids = api.get_all_contact_ids() or []
    rows: list[tuple[str, dict[str, Any]]] = []
    if not ids:
        return rows
    result = api.get_contacts(ids)
    contacts = result.get("contacts", {}) if isinstance(result, dict) else {}
    for mid, wrapper in contacts.items():
        contact = wrapper.get("contact", wrapper) if isinstance(wrapper, dict) else {}
        if not isinstance(contact, dict):
            contact = {}
        name = contact.get("displayNameOverridden") or contact.get("displayName") or ""
        rows.append(
            (
                mid,
                {
                    "mid": mid,
                    "name": name,
                    "statusMessage": contact.get("statusMessage") or "",
                    "picturePath": contact.get("picturePath"),
                },
            )
        )
    rows.sort(key=lambda row: (row[1]["name"] or "").lower())
    return rows


def _contact_name_map(api: OkLine) -> dict[str, str]:
    return {mid: data["name"] for mid, data in _contact_rows(api)}


def _resolve_names(api: OkLine, mids: list[str], names: dict[str, str]) -> None:
    """Fill ``names`` with LINE display names for any mids not already known
    (e.g. group members who aren't in our own contact list)."""
    want = list(dict.fromkeys(m for m in mids if m and m not in names))
    for i in range(0, len(want), 100):
        try:
            result = api.get_contacts(want[i : i + 100])
        except Exception:
            continue
        contacts = result.get("contacts", {}) if isinstance(result, dict) else {}
        for mid, wrapper in contacts.items():
            contact = wrapper.get("contact", wrapper) if isinstance(wrapper, dict) else {}
            if not isinstance(contact, dict):
                contact = {}
            name = contact.get("displayNameOverridden") or contact.get("displayName") or ""
            if name:
                names[mid] = name


def _resolve_to(api: OkLine, value: str) -> str:
    value = value.strip()
    if not value:
        raise WebError(HTTPStatus.BAD_REQUEST, "target is required.")
    if is_mid(value):
        return value
    matches = [
        (mid, name)
        for mid, name in _contact_name_map(api).items()
        if value.lower() in name.lower()
    ]
    if len(matches) == 1:
        return matches[0][0]
    if not matches:
        raise WebError(HTTPStatus.BAD_REQUEST, f"No contact matching {value!r}.")
    names = ", ".join(name for _, name in matches[:6])
    raise WebError(HTTPStatus.BAD_REQUEST, f"{len(matches)} contacts match: {names}")


def _message_summary(api: OkLine, msg: Any, names: dict[str, str]) -> dict[str, Any]:
    if not isinstance(msg, dict):
        return {"text": str(msg)}
    text = msg.get("text")
    encrypted = bool(msg.get("chunks"))
    if encrypted:
        if api.e2ee.is_ready():
            try:
                text = api.decrypt_message(msg).get("text")
            except Exception:
                text = "[encrypted]"
        else:
            text = "[encrypted]"
    sender = msg.get("from") or ""
    raw_meta = msg.get("contentMetadata")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    return {
        "id": msg.get("id"),
        "from": sender,
        "fromName": names.get(sender) or sender,
        "to": msg.get("to"),
        "text": text,
        "contentType": msg.get("contentType"),
        "stickerId": meta.get("STKID"),
        "fileName": meta.get("FILE_NAME"),
        "createdTime": msg.get("createdTime"),
        "encrypted": encrypted,
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    return str(value)
