# 🚀 Angel One AI Trading Bot
![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135+-009688.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

A fully automated, high-frequency algorithmic trading terminal built for the **Angel One SmartAPI**. Features a beautiful real-time local web dashboard, dynamic option chain tracking, AI-driven profit probability, and auto-scaling Stop-Loss/Target logic based on market volatility.

**Developed by Himanshu Joshi**

## ✨ Core Features
* **Multi-Index Engine:** Seamlessly toggle between NIFTY 50, BANKNIFTY, and SENSEX. The bot automatically adapts lot sizes, strike intervals, and volatility stop-losses.
* **Smart AI Risk Management:** The flagship "Smart AI (Hybrid ML)" strategy dynamically blends trend momentum with protective hedging, strictly capping risk to 5% of total account balance (Max 10 Lots) per trade to prevent blowouts.
* **Paper & Real Trading Modes:** Seamlessly switch between zero-risk simulation (with a resettable ₹100,000 portfolio) and live exchange execution.
* **Live Institutional Data:** Features stealth web-scrapers to bypass Cloudflare/bot-protection, fetching authentic daily FII & DII Net Activity and a full 21-Row Live Option Chain directly from NSE/BSE.
* **Triple-Redundancy Engine:** Prioritizes Angel One API for Live LTP and Charts, but seamlessly falls back to Yahoo Finance and Google Finance to guarantee your dashboard never goes blank.
* **Premium Bento-Box UI:** A stunning, responsive grid dashboard with 7 dynamic themes (including high-def Unsplash backgrounds), interactive Chart.js zooming, and PDF/CSV Order History exports with Grand Total calculations.
* **Physical Logging:** Writes permanent, timestamped execution logs to a local `Logs/bot_logs.txt` file for deep auditing.

## 💻 Tech Stack
* **Backend:** Python, FastAPI, Uvicorn, Angel One SmartConnect API, Async Context Managers
* **Frontend:** Vanilla HTML5, CSS3 (Glassmorphism), JavaScript, Chart.js (Financial & Zoom plugins), jsPDF
* **Database:** Local JSON File-system (`local_orders.json`, `paper_balance.json`) with Auto-Migrator
* **Automation:** Windows Batch & VBScript for seamless background launching

## 🛠️ Installation & Setup
1. Clone this repository or download the ZIP.
2. Run `Bootstrap_Setup.bat` (This automatically installs Python dependencies, checks your environment, and creates a desktop shortcut).
3. The setup terminal will securely ask for your **Angel One API Key, Client Code, PIN, and TOTP Token**, saving them to a secure `.env` file.
4. Double-click the newly created **Angel-Bot** shortcut on your desktop to start trading!
5. *Note: If upgrading from an older version, open the UI User Settings (Top Right) and click **"🗑️ Factory Reset Data"** to format the database.*

## 📜 Version History & Changelog

* **v13.1.0** - Smart AI Strategy upgraded to detect active losses and dynamically average down quantities. Enhanced NSE Option Chain Stealth Handshake. Fixed Open Price calculation fallback.

* **v13.0.0** - Fixed FastAPI Deprecation warning via Lifespan. Rebuilt Chart Timeline slicing & Native Zooming. Physical Log file writing to Logs/bot_logs.txt
* **v12.2.0** - Removed all mock OI data, built physical text Logs directory, rebuilt Chart X-axis zooming/slicing, fixed FII/DII stealth scrapers, and fixed Market Clock JS rendering.
* **v12.1.0** - Added extensive comments, fixed Market Status Colors/Glow, expanded countdown format (Days Hours), added Holiday Fallback, and fixed Active Mode UI bug.
* **v12.0.0** - Fixed Smart AI lot sizing glitch (strict 10 lot cap), added full 21-Row Live Option Chain, upgraded FastAPI to async lifespan to remove terminal deprecation warnings. Set Order History to auto-load all data on boot. Added Factory Reset endpoint.
* **v11.0.0** - Introduced "Smart AI" Strategy, prioritized Angel One API for Charts/OHLC to prevent rate limits, fixed Sensex Yahoo/Google tickers, added 5-Day chart lookback for weekends.
* **v10.0.0** - Completely separated Paper/Real order state & PnL, implemented dynamic lot scaling based on available balance, fetched live valid Expiry dates directly from Angel One instrument lists.
* **v9.0.0** - Fixed Angel One "Unknown API Error" by embedding explicit `"0"` string payloads for options pricing, strictly validating live tokens before execution.
* **v8.3.0** - Major architecture shift to Multi-Index support (NIFTY, BANKNIFTY, SENSEX). 
* **v7.1.0** - Added Dynamic CSS Theme Engine (Glass, Pro, Matrix, Ocean, Neon, Clean, Floral) and the interactive User Profile Settings Dropdown.
* **v6.1.0** - Added Institutional Data (FII/DII Net EOD) via automated background web scraping.
* **v5.0.0** - Implemented Backend Auto-Migrator to seamlessly upgrade and fix corrupted JSON database files.
* **v4.0.0** - Added Chart.js integration with real-time candlestick rendering and basic Option Chain tracking.
* **v3.0.0** - Added Base Algorithmic Strategies (Scalping, Trend, Reversal, Straddle, Strangle), continuous PnL tracking, and UI Order Logging.
* **v2.0.0** - Integrated secure pyOTP TOTP token generation, mapping Paper Trading & Real Trading modes.
* **v1.0.0** - Genesis Build. Initial FastAPI setup, WebSocket threading, and basic LTP fetching via Angel One.

## ⚠️ Disclaimer
This software is for educational and research purposes only. Algorithmic options trading involves significant financial risk. The developer is not responsible for any financial losses incurred while using this bot in "Real Trading" mode. Always thoroughly test strategies in Paper Trading mode first!
