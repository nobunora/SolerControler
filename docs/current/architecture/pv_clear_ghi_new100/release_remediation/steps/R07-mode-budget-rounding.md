# R07 mode-only開始予算の丸め誤拒否

2026-10-13、利用者が保護対象の期限比較1行と直接回帰を明示承認。変更前に承認範囲・保護履歴・追加検証を記録する。実装・ローカル検証と本番反映は別に扱う。

## 入口・参照範囲

- [保護文書](../../../../agent/PROTECTED_HISTORICAL_FAILURE_REGIONS_JA.md)を読み、引用する`d1d7792`と`f2cfa51`を直接git showで確認した。前者はSOC超過に対する制御所有者の分離、後者はmode read-back失敗後のstandbyと他設定保持の履歴証拠である。旧lease/07 gateを復活させる根拠にはしない。
- 現行[workflow.py](../../../../../../app/kpnet/workflow.py)::run_kpnet_mode_only_profileと[cloud_job.py](../../../../../../app/runtime/cloud_job.py)::_RunnerMonitorDevicePort.apply_profile、直接time-fence testsだけを変更判断のsource範囲とする。
- 本番traceは2026-10-12T18:03:51.975785Z（13日03:03 JST）にapply_profile:192→workflow:639の開始予算TimeoutError、forced_failure、目標100%、realtime SOC0%、standby成功を記録した。正確なmonotonic開始値/期限値は記録されておらず、本番が今回の丸め条件に該当したことは未確定。

## 変更対象

- workflow.py::run_kpnet_mode_only_profileのexplicit deadline枝の条件1行だけを、`operation_end - operation_start < MODE_OPERATION_START_BUDGET_SECONDS`から`deadline_monotonic < requested_end`へ変更する。
- [test_kpnet_mode_only_time_fence.py](../../../../../../tests/test_kpnet_mode_only_time_fence.py)の実KpNetClient＋模擬HTTP fixtureへ非ゼロ小数開始の回帰5ケースを追加する。新production class・関数・引数・環境値・CLI flag・依存は0。

## 変更禁止

- `HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time ownership)`、既存[protected guard tests](../../../../../../tests/test_night_soc_protected_contract.py)・保護文書を削除/緩和しない。
- `operation_start=time.monotonic()`、`requested_end=start+300`、`operation_end=min(deadline,requested_end)`、`io_deadline=operation_end−60`を保持。240秒I/O＋60秒解放予算、06:45/06:50/06:55と23/03/07の所有権、強制mode単独write/read-back、他SOC・充放電窓・契約電流・停電設定の保持は変更しない。
- _RunnerMonitorDevicePort・時刻contract・モード候補・retry・デプロイ・機器は変更しない。epsilon/iscloseや予算短縮で不足期限を許可しない。R01成果も変更しない。

## クラス・変数差分

既存`operation_start:float`、`requested_end:float`、`operation_end:float`、`deadline_monotonic:float|None`、`io_deadline:float`の所有者・default・代入は不変。production変数/class追加0。条件比較だけを既に算出した絶対期限同士へ置換する。

## 実装手順・分岐

1. 独立Pythonで`start=4095.9`、`requested_end=start+300=4395.9`、十分な`deadline=start+13920=18015.9`を検証した。minはrequested_endを選ぶのに、引き算は`299.99999999999955`へ丸まり、現guardが誤拒否する。短差は`4.547473508864641e-13`秒。2進境界4096を跨ぐ加算でULPが倍になる確定入力である。
2. 回帰を先に追加する。修正前はexplicitの十分/同値期限が失敗し、修正後は成功することを確認する。
3. 承認条件1行だけを修正。None枝は自己期限、explicit枝は`deadline < requested_end`だけを拒否し、minとI/O期限はそのまま使う。
4. 局所Ruff/mypy/compileを確認後、直接time-fence、mode-only設定保持、night_soc protected、cloud_job_runnerの関連回帰を実行する。

## テスト・PASS条件

- 回帰fixtureのclock開始値を4095.9に固定し、十分な期限18015.9・同値4395.9・Noneはforced候補3への一度のwrite/read-back成功。他の制御fieldは直前値を保持、要求開始は`requested_end−60`より前、終了はrequested_end以内。
- `math.nextafter(4395.9,-math.inf)`と`start+299`はTimeoutErrorを送信前に発生させ、HTTP requestsは0。1ULPの真の不足を丸め誤差として許可しない。
- 既存299/300予算、I/O240秒/解放60秒、時刻フェンス、23/07独立性、強制modeだけの設定差分、SOC設定保持、read-back失敗後standbyの保護回帰がPASS。
- ローカル結果は別safe artifactへ記録する。模擬HTTP合格を本番SET/read-back成功として扱わない。

2026-10-13ローカル結果: 修正前の追加5ケースは期待どおり十分/同値の2件が失敗、短期限2件は送信前拒否を確認し、None1件は動作成功。条件1行の修正後、Ruff・mypy・compile・diff checkがPASS。以下の直接/関連回帰は97 PASS（追加5ケースを含む）。source差分は承認比較1行のみ。結果とsource SHAは`artifacts/analysis/live-overnight-20261013/r07-mode-budget.safe.json`に保存する。

```powershell
python -m pytest -q tests/test_kpnet_mode_only_time_fence.py tests/test_kpnet_workflow.py tests/test_night_soc_protected_contract.py tests/test_cloud_job_runner.py
```

## 中断条件

条件以外のproduction差分、予算/時刻/モード/設定保持の変更、保護回帰失敗は停止。実行中03 JobやCloud/deviceへの操作はこのstage実装担当のscope外。本番monotonic値が不明なため原因確定・充電成功を捏造しない。

## 出口・引継ぎ

source/test/docを凍結し、条件1行・回帰件数・静的結果・safe evidenceを親へ渡す。本番反映・実03 forced SET/read-back・目標SOC達成・次retry状態・07独立動作は正式wrapperを使う親の別検証事項。
