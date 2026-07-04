# Hong Kong Holiday Calendar

合併香港政府 1823 公眾假期日曆及 Apple iCloud 香港節日日曆，產生一個同時包含一般節日與公眾假期的 ICS 日曆。

## 合併規則

- 1823 日曆中的所有事件均視為假日，事件標題結尾會加入「假日」。
- 事件以香港本地日期比較。
- 同一日期若存在任何 1823 事件，該日期的 iCloud 事件全部移除，只保留 1823 事件。
- 只有 iCloud 日曆包含的日期會完整保留，不修改事件內容。
- 同一日期若有多個 1823 事件，全部保留。

## 日曆來源

- 香港政府 1823：<https://www.1823.gov.hk/common/ical/gc/tc.ics>
- Apple iCloud：<https://calendars.icloud.com/holiday/HK_zh.ics>

## 訂閱

Repository 建立並首次執行 workflow 後，可使用以下網址訂閱：

```text
https://raw.githubusercontent.com/ezn24/Hong-Kong-Holiday-Calendar/main/hong-kong-calendar.ics
```

## 自動更新

GitHub Actions 每日香港時間約凌晨 2:17 執行。來源內容有變更時，會更新並提交 `hong-kong-calendar.ics`。

亦可在 GitHub 的 **Actions → Update calendar → Run workflow** 手動執行。

## 本機執行

需要 Python 3.11 或以上版本，不需要安裝第三方套件。

```bash
python -m unittest discover -s tests -v
python src/merge_calendar.py
```

輸出檔案為：

```text
hong-kong-calendar.ics
```

## 自訂參數

```bash
python src/merge_calendar.py \
  --official-url "https://www.1823.gov.hk/common/ical/gc/tc.ics" \
  --icloud-url "https://calendars.icloud.com/holiday/HK_zh.ics" \
  --output "hong-kong-calendar.ics"
```
