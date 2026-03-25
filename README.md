# Replit 一鍵版：Trading Insights Dashboard

這是適合手機 + Replit 的一鍵部署版。

## 在 Replit 上使用
1. 進入 Replit，建立新的 Python Repl。
2. 上傳整個專案內容，或把這包解壓後全部上傳到 Repl。
3. 在 `data/trades.json` 放入你的真實交易紀錄。
4. 點 `Run`。
5. Replit 會啟動 FastAPI 與 Streamlit，打開右側 Webview 即可看到 dashboard。

## 本機執行
```bash
pip install -r requirements.txt
python start.py
```

## 檔案格式
`data/trades.json` 採一行一筆 JSON（JSONL）或整個 JSON array 都支援。

每筆建議欄位：
- `timestamp`
- `wallet`
- `mint`
- `roi`
- `buy_ratio`
- `dev_percent`
- `holders`
- `volume_sol`
- `alpha_score`
- `strategy`
- `entry_delay_sec`

## 安全提醒
不要把私鑰放進這個專案。
