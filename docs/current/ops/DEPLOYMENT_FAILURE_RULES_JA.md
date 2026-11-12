# デプロイ失敗の再発防止ルール

毎回、production gateとlocal pre-releaseがこの文書を表示し、分離Python環境を検査する。
production gateは文書のSHA-256をstateに保存する。これは適用したルールの版の記録であり、
人間やagentが読んだことの証明ではない。実行者は出力を確認してから次の工程へ進む。

| 実際に起きた失敗 | 固定する対策・停止条件 |
| --- | --- |
| 2026-11-12: 日の出時の0.147kWhだけで共通能力係数が29.0954%増え、翌日の昼ピークが2.72749kWhへ過剰拡張 | 保存実績から太陽高度10度・方位30度ごとの能力を再構成し、別区分へ伝播させない。`test_sunrise_excess_does_not_raise_midday_and_preserves_midday_evidence`で朝・昼を分離し、未来実績除外・原本保持・新旧model provenanceと中立補正を回帰する。保存98観測の原式再生と当日24時間を照合し、global台帳の削除・currentの直接修正で回避しない |
| 同日: データなしで未書込みの取込みもactuals_saved=nullで終了し、旧実行を持つ画面が操作を再開できない | 未書込みと確定する専用例外だけfalseを記録する。`test_worker_distinguishes_no_data_from_an_unknown_write`でCSVなし・対象日なし・実書込みtimeoutを区別する。未知の書込み結果や受付不明のロック、自動再送禁止は維持する |
| 2026-11-11: 日時別予測が3,664件となり、実績取込み前の全履歴revision確認が3,660件上限で停止 | `forecast_hourly`は日付と文書IDの安定順で最大3,660件ずつ読み、境界と旧日のrevisionもdigestに含める。最大26,304件、期限、重複・非前進cursorを停止条件として保持し、途切れた履歴を公開しない。実行receiptで保存前・保存後を区別し、状態不明や保存済みの実行を再送しない |
| 2026-11-08: 承認済みの各面1.00→1.08kW/20→19度の設備整合後、同日既存計画とのsite fingerprint相違で公開を拒否し、予測Jobが失敗。旧採用計画は保持された | 設定された同一座標・timezone・その他の面別設定を維持する、この一方向の変更だけを明示識別する。新旧native geometry・site・calibration SHAを全照合し、モデル/basis/取得・学習時刻/CAS保護を残す。`test_pv_geometry_publication.py`で実際の公開guard、偽のsite/geometry/SHA、逆方向、時刻巻戻りを回帰する。失敗executionと旧採用計画の保持を保存し、同executionの再実行や保護無効化で回避しない。修正版の新commit/gate/fixture/Stateでforecast限定反映する |
| 2026-11-05: 利用中本番タブで取込みボタン無効化の申告。新規sessionの実取込みは成功したが、ソースで認証・入力拒否400/401/403も受付不明として永久無効化する経路を確認 | Job送信前の既知拒否だけpendingを解除し、未開始の理由を表示する。502/503や通信断・受付不明は引き続きロックし、自動再送しない。`test_dashboard_hourly_display.js`で5種類の応答とPOST一回を回帰する。申告タブ固有の原因は未再現であり、この経路が原因と断定しない |
| 2026-11-05: 共通時刻metadataの追加で保存原本のrun ID再構築が変わり、正式gateで直近23日がoriginal:run_mismatch、時間別744行中1248項目の照合失敗となり、本番更新前に停止 | immutable IDは従来の予測値・発行時刻・出典で固定し、派生interval_start/interval_end/forecast_soc_atだけをID入力から除く。`test_derived_time_metadata_preserves_legacy_forecast_identity`で変更前の固定IDと予測値変更時のID変化を回帰する。時刻metadata自体や原本SHA/run照合を削除せず、同commitの正式gateで31日native parityが成立するまでdeployしない |
| 2026-11-03: 06→07接続の本番mobile画像で凡例最下段が切れた。既存のcanvas描画・page overflow検査はPASSでも、legend bottom=120に対し最下段120+12となっていた | mobileの時間別chartだけ420pxを確保し、legendとplotを収める。`test_browser_rejects_clipped_hourly_legend`で観測座標を回帰し、正規browser checkerで全legend hitboxの上下端を検査する。320/390/768/1440pxの画像と実boundsを確認し、描画数/HTTP成功だけでPASSにしない。表示だけの修正では取込み・機器操作を繰り返さない |
| 2026-11-03: ボタンの本番一回試験で実績保存後の公開が停止。date refreshも全履歴epochをbootstrapへ渡し、未変更のforecast sourceを巻戻りと誤判定していた | 手動公開もbootstrap recent/history fullを別々に固定し、各uploadのnative scopeと一致させる。月次巻戻り回帰に実際のsource順序guardを組み込み、同じ最大revisionで24/744行の範囲差を検証する。actuals_saved=true・failed終端を保持し、再取込みで直さず保存済み実績を正規修復wrapperで公開する。新sourceのgate/fixture/SDK証跡を揃えるまで再受入しない |
| 2026-11-03: 一括表示修復で全履歴source epochを近日期間bootstrapにも渡し、同じ最大revisionで範囲が拡大したことを巻戻りと判定して書込前に停止 | prepareでbootstrapはrecent、historyはfullのnative query scopeを別々に固定し、公開・readbackも各scopeで照合する。`test_batch_display_repair_preserves_each_artifact_native_scope`で実際の順序guardを通す。guardを緩和せず、旧running stateと両artifactのgeneration/本文不変のreadbackを保持する。再適用せず、修正版commitの必須gate・新規prepare・レビュー済みSHAで一回だけ公開する |
| 2026-11-03: 専用実績JobのYAMLにnamespaceを明記し、gcloudが無効なCloud Resource Manager APIへ番号解決を行ってJob作成前に失敗。Web buildは成功済み | Job入力のnamespaceは省略し、正規CLIの明示--projectへ束縛する。既存plannerのnative namespaceと作成後Jobを照合する。`test_import_provisioning_uses_explicit_project_without_namespace_resolution`で番号解決APIを使わず、同commit planner imageの再利用・Job一回書込・成功状態再確認を回帰する。private cloud応答を保存し、失敗時にAPI有効化・同commit成功build再実行・state成功化を行わない。source修正は新commitのgate/fixture/Stateで進め、旧失敗証跡を保持する |
| 2026-11-03: source確認で、新日初回保存によるlatest battery選択行の移動を他日更新と誤認し得る条件と、古いhistoryで月次金額を巻き戻す経路を発見 | 未指定日全体のnative revisionは維持して照合し、派生latest selectorだけを除く。新日のcurrent-plan有無/版も固定し、採用競合時は公開しない。`test_new_day_latest_battery_selection_is_not_an_unselected_source_change`、`test_new_day_adoption_is_detected_even_while_yesterday_pointer_stays_unchanged`、`test_date_publication_does_not_roll_back_monthly_totals_to_stale_history`で境界と金額50円の保持を回帰する。未指定日の再生成や競合の成功扱いで回避しない |
| 2026-11-03: source確認で、開きっぱなしの画面の日付初期値が日跨ぎ後に過去日指定として送信される経路を発見 | 日付欄を明示変更していないrequestは日付を省略し、サーバー受付時のJST当日を固定する。`test_dashboard_hourly_display.js`で日跨ぎ未指定・明示過去日保持・二重POST防止を回帰する。旧初期値を自動で過去日明示へ読み替えない |
| 2026-11-03: 正時SOC表示fieldをFirestoreだけに追加し、SQLiteとの実データ照合744行が不一致。正時修正後も保存夜間予測の68行で出典が欠落 | 両backendで同じ元timestampの正時SOCを付加し、夜間予測はSQLiteの専用read modelへ値と出典を保存する。`test_sqlite_hour_start_soc_matches_common_reader_and_keeps_forecast_identity`と`test_validation_sync_keeps_retained_night_forecasts_out_of_primary_storage`で0・半時代用禁止・run/issued-at保持・読取DB不変・逆同期除外を回帰する。native backend parityを再実行し、fieldの除外やgateのskipで通さない。本番反映・表示データ修復は照合成功まで停止する |
| 2026-11-03: 実績充放電係数で保存したSOCを旧容量換算の照合器が拒否し、bootstrap書出し前検証も同じ誤判定で停止。期間指定APIは新原本、初期表示APIは旧原本となった | 予報照合とbootstrap書出し前検証の両方へ同じ保存原本のbattery_response（時間別係数・driftを含む）を渡す。旧原本は旧計算を維持。`test_frozen_empirical_response_controls_soc_evidence`と`test_bootstrap_publication_uses_frozen_empirical_response`で数値例・改変拒否を回帰する。原本SHA・17点・表示一致・公開世代の保護を緩めない。本番はbootstrap APIと期間指定APIの発行時刻/SOCを同じ原本と照合し、Job成功や描画PASSだけで合格にしない |
| 2026-10-15: テストfakeに消費学習入力がなくAttributeError | fixtureを実際の境界契約へ合わせ、関連テストと全体gateを再実行。テストをskipしない |
| 同日: 通常Pythonで使えるtzdataが `python -I` では見えず、image import試験が失敗 | 最初に同じ分離モードでAsia/Tokyoをロード。失敗したら使用interpreterの環境を修復。自動のglobal pip installはしない |
| 同日: pytestのfd capture下でgit子プロセスがWinError 6 | 正規local/integration入口は `--capture=sys` を明示。テスト選択や件数は削らない。ログredirect方法だけの変更では解消しなかった |
| 同日: Cloud Runの省略されたrunningCountをStrictModeで直接参照して診断が失敗 | exact executionを正規wrapperで照合。欠落fieldはUNKNOWN。診断失敗をCloud失敗とみなして再実行しない |
| 2026-10-16: API/Job成功後のbrowserで全て→1ヶ月の日付逆転と390px画面の横溢れを発見 | 表示対象の変更ではbrowser受入を別実行。初期表示、全て→1ヶ月、mobile、consoleを確認。FAIL/未実施のまま一連の確認をPASSと報告しない |
| gateが任意skipを含んでもpassedを記録できた（実装レビューで発見） | 全checksがsuccessの場合だけpassed。省略したgateは非ゼロ終了。過去stateも個別checksを確認 |
| 2026-10-16: 新しい前提検査をCIの依存導入前に呼び、未導入tzdataで停止 | CIは依存導入→前提検査→品質gateの順。コマンド存在確認を依存ロード検査へ拡張するときは呼出側の実行順も検証する |
| 同日: 期限テストの偽時計初期値がhost稼働時間由来で、丸めにより60秒を僅かに超過 | 偽時計は正確に表現できる固定値から開始する。実時間を混ぜず、期限後の再要求拒否のassertionを維持する |
| 同日: 売電契約開始後も保存計画がneutral/収入0で、残量価値も費用比較から欠落 | `SOC_NET_COST_CONTRACT_JA.md`と契約設定を確認。`test_soc_financial_contract.py`とCSV金融入力回帰を通常gateで実行し、原CSVの請求期間買電累積と計画値、対象日単価、残量控除、費用内訳合計を照合。不一致・契約切替日の未確認は停止する |
| 同日: 全体pytest実行中に契約設定とテストを編集し、読み込み済み旧テストと新設定が混在 | gate開始後はsource/config/testsを編集しない。変更が入った実行は受入証拠に使わず、固定commitから正規gateを再実行する |
| 2026-10-31: 理想最大台帳の日付跨ぎ本番試験で、初期化後の区間に対応する初期化前の発行値を運用評価1件と誤認 | 区間開始だけでなく保存原本のissued_atもinitialized_at以降であることを必須にする。前・同時刻・後の境界を`test_post_initialization_hour_requires_a_post_initialization_issue`で回帰し、事前発行証拠なしはUNVERIFIED_ISSUE_TIMEで停止する。原本・係数・学習結果の改変や、評価件数0を精度成功へ読み替えることは禁止 |
| 同日: 遅い時刻の同日予測再保存で、原本にない古い夜間SOCがFirestoreの再帰mergeで残った | forecast_plansの所有mapは親field maskで置換し、欠損の時間別SOC/充電量はnullにする。実SDKのwhole-map-mask回帰と履歴保護回帰を必須とする。既存派生値は保存原本・対象世代を固定した専用repair wrapperでだけ修復し、結果不明時は再書込せずverifyする。本番実行後は当日を含むbounded原本照合を行い、未確定点の補間や原本書換えで通さない |
| 同日: 故障注入で独立した理想最大台帳の保存失敗が完了済み実績取込を異常終了させ、通常forecastを止めることを再現 | 台帳境界だけで例外を構造化ERRORとして記録し、通常forecastへ伝播させない。全3backendで通信・整合性・I/O故障を回帰し、実績取込本体の失敗は従来どおり停止する。台帳自身のCAS/checksum/古いcopy拒否を緩和しない。新しいrelease gate・Scheduler・自動retryは追加しない |

## 毎回の順序

画面の期間・レイアウト変更では次の回帰を必須とする。

- bootstrapとhistoryは生成時刻が異なる。古いhistoryで最新日・締め日設定を巻き戻さない。
  請求締め日を跨ぐfixtureで「全て→1ヶ月」のラベル・グラフ日付一致を検証する。
- 長いモデルSHA・数式・容量値・予実レビューKPIを320/390/768/1440pxで実ブラウザ描画する。
  document全体の横溢れと文字欠けを禁止し、ガント内の横スクロールを維持する。
  bodyのoverflowを隠して合格させない。
- `test_dashboard_bootstrap.js`と`test_dashboard_responsive_layout.py`を通常gateで実行する。
  CIは既存PlaywrightのChromiumを明示導入する。ローカルも同browserが必要で、未導入はskipしない。
- 初期history要求0回、明示遷移後1回、月へ戻る際の追加要求0回を維持する。

1. 本番runbook全文を読み、承認済みscope・固定commit・元StatePathを確定する。
2. `scripts/pre_release_local.ps1 -CheckPrerequisites` は早期診断だけ。正式検査は
   clean commitで `scripts/production_deployment_gate.ps1 -RunPreRelease` を実行する。
3. 全必須checks成功を確認。新しいcommitでは古いgate/fixture/receiptを流用しない。
4. 正規wrapperでのみ反映する。timeout/running/unknownではexact executionと復元を確認し、
   元StatePathのResumeへ進む。新build・実機SETのblind retryは禁止。
5. デプロイ、実server SDK、予測Job、browser、実機を別々に判定する。
   forecast/dashboard scopeで07 DryRunや実機往復を追加しない。
6. browserは `scripts/check_dashboard_browser_from_env.ps1 -InventoryPath <private inventory> -OutputDirectory <artifacts配下の新規directory>`。
   同じ本番URLへの認証だけを許可し、receiptと画像を保存する。終了0は記載した表示チェックの合格のみ。
   未導入Playwright/Chromium・認証失敗・timeoutはINCONCLUSIVEで停止。勝手にinstall/retryしない。
   PNGも目視し、予測値・実績・SOC・出典の正しさは当該変更のAPI/原本照合で別途検証する。

この表は既知障害の再現防止であり、将来の全障害が発生しない保証ではない。
新しい失敗を追加するときは観測根拠、実行可能なチェック、停止条件を同じ変更に含める。
