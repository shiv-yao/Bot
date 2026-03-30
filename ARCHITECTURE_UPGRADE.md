# 升級後架構說明

這次升級是 **加層、不砍功能**。

保留的既有模組：
- `alpha_engine.py`
- `alpha_boost_v3.py`
- `allocator.py`
- `bot.py`
- `portfolio_manager.py`
- `state.py`

新增的可實裝核心層：
- `core/models.py`：統一 `SignalEvent / OrderIntent / FillEvent`
- `core/signal_bus.py`：事件匯流排
- `core/alpha_adapters.py`：把舊 alpha 包成一致輸出
- `core/portfolio.py`：持倉帳本，和 `engine.positions` 同步
- `core/architecture.py`：新的 orchestrator
- `main_upgraded.py`：新的入口範例

## 設計原則

1. 不刪舊 code，舊流程照樣能跑。
2. 新功能採 adapter / orchestrator 包裝，方便逐步遷移。
3. 對外狀態仍維持 `state.engine`，避免前端與監控 API 壞掉。

## 建議遷移順序

### 第 1 步：先用新的 Signal 格式
把新的 alpha 或額外來源，先接進 `core/alpha_adapters.py`。

### 第 2 步：在 `bot.py` 內導入 orchestrator
把現在散落在 `bot.py` 的 alpha 蒐集流程，逐步改成：

```python
from core.architecture import build_default_orchestrator

orchestrator = build_default_orchestrator()
signals = await orchestrator.collect_signals(CANDIDATES)
orders = orchestrator.build_orders(signals)
```

### 第 3 步：成交後同步到 PortfolioBook
如果買入成功，建立 `FillEvent` 後呼叫：

```python
orchestrator.portfolio.apply_fill(fill)
```

這樣 `engine.positions` 會自動同步，前端不用改。

## 為什麼這樣改比較安全

- 舊 API / 舊部署方式不需要一次全部翻掉。
- 可以一個模組一個模組替換。
- 出問題時容易 rollback。

## 下一步最值得做的兩件事

1. 把 `bot.py` 的買入/賣出事件改成 `FillEvent`。
2. 把 `allocator.py` 擴充成真正的 strategy-aware sizing。
