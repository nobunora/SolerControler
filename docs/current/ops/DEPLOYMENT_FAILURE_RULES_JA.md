# デプロイ失敗の再発防止ルール

毎回、production gateとlocal pre-releaseがこの文書を表示し、分離Python環境を検査する。
production gateは文書のSHA-256をstateに保存する。これは適用したルールの版の記録であり、
人間やagentが読んだことの証明ではない。実行者は出力を確認してから次の工程へ進む。

| 実際に起きた失敗 | 固定する対策・停止条件 |
| --- | --- |
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
