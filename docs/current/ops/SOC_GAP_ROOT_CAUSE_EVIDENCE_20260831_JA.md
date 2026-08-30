# 2026-08-30 SOC不足: 原因分析の固定証拠

## 1. この文書の目的

この文書は、2026-08-30朝のSOC不足について、Codex/Luna軽が解釈を広げずに実装できるよう、現時点でGit上から確定できる事実・推定・未確定事項を分離して固定する。

ここに書かれていない推測を実装理由に追加してはならない。

---

## 2. 確定事実

### 2.1 実績SOC

`docs/completed/reports/soc_gap_evidence_2026-08-30.csv` により、対象夜間のSOCは次のとおり。

```text
2026-08-29 23:00  0%
2026-08-30 00:00  0%
2026-08-30 01:00  0%
2026-08-30 02:00  0%
2026-08-30 02:30  0%
2026-08-30 03:00 15%
2026-08-30 03:30 34%
2026-08-30 04:00 54%
2026-08-30 04:30 68%
2026-08-30 05:00 76%
2026-08-30 05:30 76%
2026-08-30 06:00 76%
2026-08-30 06:30 76%
2026-08-30 07:00 75%
2026-08-30 07:30 73%
```

よって「03時以降に一度は充電した」「05:30以降は目標未達のまま充電0kWh」という2点は確定。

### 2.2 計画と実行SOCの乖離

`docs/completed/reports/soc_gap_investigation_2026-08-30.md` は次を記録している。

```text
計画生成時 soc_now_percent = 94%
計画 required_night_charge_kwh = 0.601 kWh
03時実機SOC = 0%
03時再計算 required_night_charge_kwh = 9.864 kWh
目標SOC = 100%
07:30実績SOC = 73%
```

この差は表示誤差として扱わない。

### 2.3 旧03制御はmonitorとactuatorでSOC権威が分かれていた

`72697aba6a5567959e5d766b81d7ee378557709d` 世代の03 monitorは、realtime SOCから必要量を再計算していた。

一方、forced設定は `dynamic_forced_profile=True` で別processのKP-NET workflowへ渡され、`app/kpnet/profile_builder.py::_build_dynamic_forced_profile()` はplanファイルの以下を再読込していた。

```text
plan.soc_now_percent
plan.required_night_charge_kwh
plan.target_soc_7_percent
```

したがって旧構造では次が同時に成立し得た。

```text
monitor側: realtime SOC=0%なのでほぼ満充電分が必要
actuator側: plan SOC=94%なので0.601kWh程度でよい
```

これは構造上のsplit-brainであり、旧実装の欠陥として扱う。

### 2.4 現行03制御は旧split-brainをそのまま使っていない

`badd209df2b7ba007191abcc28af9aed20c02f45` 以降の現行03 monitorは、forced startで `run_kpnet_mode_only_profile(profile="forced")` を使い、旧dynamic plan profileを使っていない。

したがって、旧dynamic profileを復活させる修正は禁止。

---

## 3. 既知の正常境界として比較するcommit

### 3.1 第一比較基準

```text
72697aba6a5567959e5d766b81d7ee378557709d
fix: force charge through 50 percent device candidate
```

このcommitをforced-command契約の既知正常境界として使う。

理由は次の4点に限定する。

1. 実機 `SocChargeMode` 最大候補50%を確認済み。
2. 計画51〜100%でも最大候補50でforced開始する。
3. raw planning targetを03 monitorの停止閾値として維持する。
4. post-deploy live probeで forced / SocChargeMode=50 / read-back / 60秒保持 / snapshot復元を検証する。

このcommit全体をリバート対象にしてはならない。

### 3.2 現行time ownership基準

```text
badd209df2b7ba007191abcc28af9aed20c02f45
fix: isolate scheduled battery control by time
```

このcommit以降の以下は維持する。

```text
23:00 standby一回
03:00 standalone forced monitor
06:45 forced monitor cutoff
06:50 final standby start cutoff
06:55 all 03 I/O hard cutoff
07:00 green一回
07 greenは03失敗から独立
03 forced reapplyなし
```

これらを元に戻す変更は禁止。

---

## 4. 現行実装に残る確定した弱点

### 4.1 `mode-only forced` がfull static profileを送っている

現行 `app/kpnet/workflow.py::run_kpnet_mode_only_profile()` のforced分岐は概念上「mode-only」だが、実装は:

```python
selected = replace(
    FORCED_CHARGE_PROFILE,
    battery_operating_mode=forced_code,
)
```

である。

その後 `_apply_settings_profile()` → `_build_payload()` に入り、provider formにはmode以外の複数fieldも送られる。

`FORCED_CHARGE_PROFILE` の固定値には少なくとも:

```text
socSafetyMode=50
socContactInput=100
socChargeMode=50
chargeStart=04:30
chargeEnd=06:30
```

が含まれる。

よって現行03 forcedは、現在値を保ったmode変更ではなく、古い固定profileを再注入し得る。

これはコード上確定している。

### 4.2 live probeとproduction 03 forcedの契約が一致していない

`app/kpnet/settings_roundtrip.py::make_reversible_probe_profile()` はcurrent snapshotを基準にして:

```text
batteryOperatingMode = forced
socChargeMode = supported candidate
```

を上書きし、他のcontrolled fieldを維持する。

一方production 03 forcedはstatic `FORCED_CHARGE_PROFILE`を基準にする。

したがって、デプロイ時live probeで成功した経路と、03時production forced経路のpayload契約が一致していない。

これを修正対象とする。

---

## 5. read-back mismatchログの扱い

調査レポートはCloud Runログとして:

```text
2026-08-28T21:24Z ～ 2026-08-28T21:29Z
```

を記載している。

これはJSTでは:

```text
2026-08-29 06:24 ～ 06:29
```

であり、今回の実績CSV対象:

```text
2026-08-29 23:00 ～ 2026-08-30 07:30 JST
```

と1日ずれる。

よって、現時点では `batteryOperatingMode read-back mismatch` を2026-08-30朝の直接原因として確定してはならない。

このログは、provenanceが再確認されるまでは:

```text
直前に実際に発生した同種のKP-NET read-back障害の証拠
```

として扱う。

---

## 6. source修正の固定結論

今回の最小source修正は次の1点に限定する。

```text
03 forced profileを
「static FORCED_CHARGE_PROFILE基準」
から
「current device snapshot + forced mode + max supported SocChargeMode」
へ変更する。
```

同時にread-back mismatch時のrequested/observed controlled valuesを非機密summaryへ残す。

planner SOC freshness改善は別patch候補として記録し、この修正へ混ぜない。

---

## 7. 禁止する結論

以下はこの証拠からは導けないため、実装してはならない。

- read-back mismatchを無視すれば直る
- 03 reapplyを戻せば直る
- 07 greenを03成功に再依存させれば直る
- forced charge windowを03:00〜06:45として新規書込みすれば直る
- plan SOC 94%を単純に0%へ置換すれば直る
- `SocChargeMode=50`を停止目標50%として使う
- `NIGHT_SOC_READBACK_REQUIRED=false`へ変更する
- time fenceを06:45/06:50/06:55より後ろへずらす

以上。