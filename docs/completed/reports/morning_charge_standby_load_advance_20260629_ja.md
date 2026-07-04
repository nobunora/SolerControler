# 04:00夜間制御・家庭負荷補正・待機維持 実装報告

> 注記: このレポートの「家庭負荷補正による開始前倒し」と「7時逆算開始」は、2026-06-30の04:00即時強制充電方式への変更で廃止済みです。現行仕様は `docs/current/product/CURRENT_DECISION_TREE_JA.md` を参照してください。

作成日時: 2026-06-29 JST

## 背景

6/27-6/29の朝SOC実績を確認したところ、6/29は家庭負荷が高く、買電上限付近で充電電力が押し下げられていた。
また、目標到達後にグリーン/放電系へ戻すと7時前後に放電が始まり、経済的に不利だった。

## 変更内容

- `cloud_job_runner.py`
  - 03系夜間制御の後段切替先を `ADJUST03_POST_CHARGE_HOLD_PROFILE=standby` で指定可能にした。
  - 設定SOC到達後は、ターゲットSOCに関係なく待機モードへ切り替える。
  - タイマー到達時も待機モードへ切り替える。
  - 直近朝負荷から開始前倒しを計算する旧設定群を追加。
  - 03-monitor scheduleのdetailへ家庭負荷補正の理由と負荷値を保存する。
- `app/kpnet_workflow.py`
  - `KP_FORCE_SETTINGS_PROFILE=standby` を追加。
  - BatteryOperatingMode候補から `待機/standby` を選択できるようにした。
- `scripts/deploy_gcp_jobs.ps1`
  - `solar-battery-run-03` を `04:30` から `04:00` へ前倒し。
  - standby維持と家庭負荷補正の環境変数をCloud Run Jobへ追加。
- `.env.example`, `README.md`
  - 04:00運用、standby、家庭負荷補正の説明を追加。

## 実機候補確認

KP-NETのBatteryOperatingMode候補を読み取り、待機相当のコードが `5` と判定されることを確認した。
この確認は読み取りのみで、設定変更はしていない。

## 検証

- `python -m pytest tests\test_cloud_job_runner.py tests\test_kpnet_workflow.py tests\test_energy_model.py -q`
  - 53 passed
- `python -m compileall cloud_job_runner.py app\kpnet_workflow.py`
  - 成功
- `python scripts\security_check.py`
  - 成功

## 注意

- ターゲットSOCによる特別分岐は入れていない。
- 家庭負荷補正は直近CSVの04:30-07:00負荷から算出する。
- 待機モードの実機挙動は次回04:00実行後のSOC推移で確認する。
