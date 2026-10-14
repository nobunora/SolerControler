# 本番デプロイ再現手順

この手順は、Windows / PowerShell 7 環境から本番を安全に検証・デプロイし、途中終了した場合も成功済み工程を再利用して再開するための標準手順です。

## 適用範囲

- Cloud Run Job（23時、03時、07時）のビルド・更新
- 02:30 forecast-only Cloud Run Job の限定更新
- Cloud Scheduler の確認
- 本番設定の検証
- dashboard Cloud Run service のビルド・更新
- デプロイ後のDryRunまたはdashboard read-only受入確認

認証情報、project、region、resource IDはGit管理外の `.env` と `scripts/production_env.ps1` から取得します。値をコマンド、tracked file、報告、チャットへ転記しません。

## 0. 固定した実行経路と禁止手順

コマンドを作る前にこの節で経路を確定する。成功した既存wrapper・build設定を使い、同等に見える別コマンドを組み立てない。

| 対象 | 固定する成功経路 | 完了判定 |
| --- | --- | --- |
| 共通準備 | 最終commit・clean worktree → `production_deployment_gate.ps1 -RunPreRelease` → 正式wrapper。preflightの`repository_commit`がHEADと一致し、`status=passed`かつ`checks`配列の必須項目がすべて`success`であることを確認 | source/DB backup、security、ValidateOnly、全体・統合検査を省略しない |
| 分離構成の画面限定 | `deploy_production_from_env.ps1 -DeploymentScope dashboard -StatePath ...` → `cloudbuild.web.yaml` / `Dockerfile.web` → commit固定imageをdigestへ解決 → service imageだけを更新 | created/ready revision一致、Ready=True、新revisionへtraffic 100%、API/browser受入、Job/Scheduler/IAM/env/secret不変 |
| 分離構成の全体反映 | 同wrapperの`-SeparatedRuntime`又は環境設定による分離経路。`auto/full`の既存工程を順に実行 | 同commit/digestのSDK証拠、原本・派生一致、実機read-back/60秒保持/復元、backup、最終verification。個別stage成功を全体成功へ読み替えない |
| 中断からの再開 | 実行・復元をread-only照合 → 元のcommit/layout/scope/StatePathを保持 → 正式wrapperの`-Resume` | `success`工程と一致するidentity/receiptだけを再利用。未成功工程は完了確認後に正規の再開判定へ渡す |

固定の根拠は2026-10-14の全12工程成功と、その後の画面限定成功である。これらは特定commitの実証であり、将来の変更のgateを免除しない。legacy構成の互換経路は2.0節と各scopeの条件に従い、分離構成の成功証拠を流用しない。

### コマンド生成時点で禁止する操作

| 失敗・誤判定しやすい操作 | 禁止手順 | 使用する代替手順 |
| --- | --- | --- |
| layout/buildの混同 | 分離webへ`Dockerfile.dashboard`やlegacy依存を使う、画面限定要求を`full`へ拡大する、既存env/IAMを作り直す | 上記の明示dashboard経路。scope拒否は停止し、承認範囲内の経路を調査する |
| 手組みbuildと引数の崩れ | credential-bearingな`gcloud`の再構築、複雑なfilter/JSON/CLI flagの文字列連結、Windows cmdの引用符へ依存する再実装 | repository wrapperの検証済み引数渡し・build YAMLをそのまま使う。引数/quote変更は関連回帰と全体gateを通す |
| ログ選択・JSON解析の失敗 | `labels=null`をmapとして参照する、UTC日時の暗黙cast、欠落したfieldを0/false/成功とみなす、filter不一致を通信遅延と決めつけてretryする | 既存のbounded exact-execution receipt選択・safe summaryを使い、schema/任意fieldを確認する。build、SDK、receipt取得/解析を別判定する |
| 外側timeout後の重複実行 | `running/unknown`のまま新build・新D・新実機往復を起動する | exact executionの終端と復元をread-only確認してから、同StatePathのResume。待機終了だけではCloud失敗と判定しない |
| イメージ・fixtureの取り違え | mutable `latest`を今回の成功根拠にする、新HEADに旧fixture/承認SHA/採用receiptを付け替える | commit/digest/fixture SHA/receipt identityを照合する。source変更時は新gate・prepare・formal stateを作る |
| 手動skipによる取りこぼし | build/job/import/backupのskip flagを並べて部分再開を代用する、失敗stateを書き換える | 3節の正規Resume。`-SkipPreRelease`は同commitの必須preflight全成功又は同stateのpre-release成功がある場合だけ使用する |
| SOC取得だけの一時失敗 | SOC read失敗を理由にforced/greenのSET・保持・復元を自動で繰り返す | 既存SOC readerの読み取りだけのretry（最大3回・全体60秒）とsafe診断を使う。復元確認と失敗記録を保持する |
| 成功証拠の混同 | build SUCCESS、HTTP 200、Job受付、local fakeのPASSだけで本番全体や実機成功とする | 対象scopeの4節の全条件を確認。scheduled 03/07はその実executionとdevice read-backを別途確認する |

表の禁止事項は診断のための低レベルread-only操作を禁止するものではない。診断で成功経路を置き換えず、機密値を出力せず、元のfailed/unknown記録を保持する。

## 1. 事前確認

### SOC API 認証と実測確認（2026-10-10）

SOC はアプリの `get-measurement-data/v2` に `measureFlg=GW` を送って取得する。
Web HTML と遅延CSVのSOC取得経路は削除済み。秘密のJSON束はGit管理外 `.env` の
`KP_SOC_API_CREDENTIALS_JSON`、Secret Manager名は `KP_SOC_API_CREDENTIALS_SECRET` に設定する。
値を表示・ログ・PRへ記録しない。ローカルの読み取り専用確認は
`scripts/run_soc_api_probe_from_env.ps1` を使う。

役割分離版の正式デプロイは `soc_api_credentials` 工程で
`scripts/configure_soc_api_from_env.ps1` を実行し、03監視と検証Jobだけにsecretを接続する。
`-ValidateOnly` はJSON構造だけを検証し、secretやJobを変更しない。
実機往復試験の前後で同じSOC取得経路を実行し、測定時刻・取得時刻・SOCを記録する。
認証キーが失効・更新された場合は同じ正式経路で差し替え、取得試験を再実行する。
API取得成功だけでは充電停止や設定復元の成功とは扱わず、従来の実機往復試験も維持する。

```powershell
git status --short
pwsh -NoProfile -File scripts/production_deployment_gate.ps1 -RunPreRelease
```

次の全条件を満たすまでデプロイしません。

- 作業ツリーがcleanである
- ローカルsource/DBバックアップが成功している（`-SkipLocalBackup`は禁止。バックアップは`.env`を含めない）
- `security_check.py`、関連テスト、全体回帰テストが成功している
- `.env` がignore対象で、stageされていない
- `-ValidateOnly` が必須設定を検証し、「No deployment was performed」と表示する

ゲートは `artifacts/deployment_state/preflight-*.json` に非機密の判定結果を保存します。ゲートが失敗した場合は本番ラッパーを起動しません。

以下の例の`-SkipPreRelease`は、このゲートの全必須チェック成功と同commitを確認した直後の重複検査を省くための指定です。gateの`status=passed`だけでは、任意skipしたチェックの成功を証明できません。`checks`配列の各`name/status`も確認し、保存済みpreflightのパスを運用記録に残します。wrapper側の`pre_release=skipped_manual`は外部ゲート実行の証拠ではなく、外部preflightと組にして判定します。

## 2. 通常デプロイ

### 役割分離版への移行と継続運用

2026-10-07に承認された役割分離版は、計算・制御・画面を用途別にbuildする。
既存wrapperでは単一runnerを再配布してしまうため、正式入口に`-SeparatedRuntime`を追加した。
`scripts/deploy_production_from_env.ps1 -SeparatedRuntime -StatePath artifacts/deployment_state/production-<開始時刻>.json`を使う。
移行後はGit管理外`.env`の`SOLAR_RUNTIME_LAYOUT=separated`で通常入口もこの経路に固定する。
この移行経路は`auto/full`を受け付ける。明示`dashboard`は画面限定、明示`forecast`は下記の計算限定経路へ進む。それ以外の非制御scopeを指定したときは制御操作へ拡大せず、明示的に拒否する。
`SOLAR_RECOVERY_BACKUP_PATH`には保存済み完全バックアップのローカルパスを指定する。
ゲート、保存済み原本との比較、運用設定backup、3役割build、計算Job更新・実行、
同じ制御imageでのforced/green各SET/read-backと60秒保持・元モード復元、制御Job更新、
画面更新、公式07 DryRun、Drive backup、digest/ready revision検査の順に記録する。
計算のCSV/model成功証跡は保存計画に含め、制御probeが同じ原本の鮮度・日付・SHAを検証する。
制御側ではCSV/model計算を繰り返さない。機器試験は既存mode-only往復処理を使い、SOC設定は変更しない。
`-Resume`で省略できるのは`success`工程だけ。制御工程`running`は復元・実行終端の調査前に再実行しない。
旧imageの削除は、このwrapperの完了だけで許可しない。実データ一致、画面確認、
現行imageのOCI保存・SHA検査、全Job/service/復帰revisionの参照確認を別途要求する。

移行完了後の承認済み計算修正では、`-ApprovedCalculationPatchSha256` にレビュー・テスト済みの
canonical計算差分のSHA-256を明示する。対象はparity検査が従来から保護している計算・公開ソース。
未指定なら従来どおり差分を拒否し、指定時もその正確な差分以外を拒否する。
保存原本の一致・旧監視との操作/時刻トレース検査は引き続き必須。stateに差分ハッシュを保存し、
Resumeでも同じ承認値を要求する。検証済みコミットへの固定と通常の本番ゲートは省略しない。
今回のPV欠損修正を、移行時の計算一致検査の恒久的な無効化として扱わない。

通常の制御反映は07:15〜22:29 JSTに行う。時間外の明示承認された一回限りの反映では
既存の `-AllowOutOfWindowLiveProbe` を正式入口から渡し、stateへ記録できる。
並行Jobの拒否、実機mode-only往復、60秒保持、元設定のread-back、03/07の時刻所有権は維持する。

最初は必ず高レベルの公式ラッパーを使用します。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipPreRelease `
  -SettingsRoundTripTargetSoc 50 `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
```

低レベルスクリプトや独自の `gcloud` 更新コマンドへ置き換えません。
デプロイラッパーは工程ごとに状態を書き込みます。`running` のまま終了した工程は成功扱いにしません。

### 2.0 非制御scope: dashboard-only / forecast-only

`-DeploymentScope control-readonly` は control Job を更新できない（即時エラー）。23/03/07 の image-only 反映は `scripts/deploy_control_jobs_image_only.ps1` を使い、clean worktree と immutable digest を確認した上で、CSV/plan/実機 settings round-trip（60秒保持・read-back 復元）を含む必須 live probe が成功する場合だけ完了とする。probe が失敗したら既存 image へ rollback し、完了扱いにしない。

`-DeploymentScope dashboard` は dashboard Cloud Run service だけを更新する正式な非制御経路です。このscopeではrunner、23/03/07、02:30 forecast job、Scheduler、KP-NET import、Drive backup、機器settingsを変更しません。

`SOLAR_RUNTIME_LAYOUT=separated` または `-SeparatedRuntime` の場合も、明示`dashboard`は画面限定で受け付けます。`cloudbuild.web.yaml`で既存の`Dockerfile.web`・web依存関係を使用し、現在の画面imageをcacheにしてcommit固定のimageを一度buildします。digestへ解決してserviceのimageだけを更新し、同imageのcreated/ready revision一致とReady=Trueを確認します。既存service account、IAM、env、secretは保持し、従来のdashboard環境変数再構築を通しません。分離構成の`auto/full`は従来の完全経路を維持し、`runner/control-readonly`は引き続き拒否します。通常の事前gate、状態記録、browser/API受入、非制御scopeの機器試験非該当条件は省略しません。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -DeploymentScope dashboard `
  -SkipPreRelease `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
```

`-DeploymentScope forecast` は共有runner imageを1回buildし、`solar-forecast-daily`だけを新しいimageへ更新する正式な非制御経路です。23/03/07 Job revision、settings-roundtrip Job revision、dashboard、forecast Scheduler、KP-NET import、Drive backup、機器settingsを更新しません。下位のcanonical deploy scriptが既存23/03/07 Scheduler定義を再assertする場合があるため、forecast-onlyの合格判定ではdeploy前後のschedule/time-zone/targetをread-only比較し、**実効変更が0**であることを必須とします。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -DeploymentScope forecast `
  -SkipPreRelease `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
```

この`forecast` scopeは明示指定専用です。`auto`が`app/`差分をrunner/fullと判定した場合でも、レビューで変更がforecast-only ownerに限定され、23/03/07を更新しないことが証明できる場合にのみ使用します。

分離構成では同じ正式入口の明示`forecast`を`deploy_separated_forecast_from_env.ps1`へ渡す。最終merged commitの全必須preflight成功と、そのcommitに固定した`AdapterProofFixturePath`、明示`StatePath`を要求する。`cloudbuild.planner.yaml`で`Dockerfile.planner`だけを一度buildし、固定digestの既存SDK proofを成功・cleanup・専用検証Job復元まで確認してから、`solar-forecast-daily`のimageと既存の`PLAN_SOURCE_REVISION`/`PLAN_IMAGE_DIGEST`だけを更新する。23/03/07、backup、Scheduler、web、IAM、secret、計算条件は変更しない。SDK proofは専用settings-roundtrip Jobのテストcommandを一時更新・復元するため、そのJobのrevisionは変わり得るが設定specの前後一致を必須とする。機器SETは実行しない。通常制御Jobのrevisionは維持する。画像buildとSDK proofを別stageで記録し、SDK失敗を理由に成功済みimageを再buildしない。running/unknownからのblind Resumeは禁止する。

計算側と画面の共通表示ロジックを修正した場合は、同じcommitで明示`forecast`と明示`dashboard`を別StatePathで実行する。計算Job実行・表示データ再生成は既存`run_cloud_job_from_env.ps1 -Slot forecast -ExecutionReceiptPath ...`で終端と失敗task 0まで確認する。bootstrapに加え、既存のbounded `/api/dashboard?end_date=...&window_days=...`で請求境界を検証する。機器試験や通常制御Jobの手動実行を追加しない。

`dashboard`と`forecast`はいずれもdeployed control ownerを変更しないため、実機settings round-tripは実行せず、stateの `settings_roundtrip.status` を `skipped_not_applicable` として記録します。これは手動skipではなく、非制御scopeで制御系を触らないための標準契約です。`skipped_manual`、`running`、`failed` をこれと同一視しません。

この例外は`dashboard`と`forecast`に限ります。`runner`または`full` scopeでは、以下のsettings round-tripを従来どおり必須とし、省略・warning化・任意化しません。

runner/full本番デプロイ後は、設定テストを必ず実行します。この工程はSchedulerを持たない専用Cloud Run Jobで、実機が受け付ける最大`SocChargeMode`である**50%**と強制充電モードを設定し、read-backで強制充電の設定を確認します。保持時間は**60秒固定**で、その後デプロイ直前のcontrolled settings全項目へ復元してread-backします。計画SOCが50%を超えても、これは機器設定の離散値であり、03時ジョブが連続値の計画SOC到達で待機へ切り替えます。強制充電設定、変更、または復元のいずれかが失敗した場合は、復元後に工程を失敗として停止します。

`-DeploymentScope auto`（既定）は、最後に成功した本番コミットとの差分からrunnerだけ、dashboardだけ、両方、または対象なしを判定します。forecast-onlyは安全性のため自動推定せず明示指定します。runner/control Cloud Runを更新する`runner`/`full`ではsettings round-tripを必須とし、`dashboard`/`forecast`では上記の非制御契約に従って`skipped_not_applicable`とします。

実測から、事前ゲートの待機上限は**5分**、完全デプロイの待機上限は**25分**とします。外側の短い対話タイムアウトで本番工程を終了させず、状態ファイルを監視して完了または失敗を確認します。

### 2.1 短縮経路（検証を維持する場合）

通常のrunner/full経路ではジョブ更新中にも07時ジョブのDryRunを実行します。デプロイ後の公式DryRunを別途実施する場合は、次のオプションで重複実行を避けられます。

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -SkipInlineSmokeTest `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

`-SkipInlineSmokeTest` は検証を省略する指定ではなく、runner/fullのデプロイ工程内の重複待機を省略する指定です。runner/fullの合格判定には、必ず後段の公式DryRunとCloud Run executionの4条件確認を使用します。dashboard/forecastの非制御scopeでは07 DryRunを新規実行しません。

runner／dashboardのCloud Buildは直前のArtifact Registryイメージをキャッシュ候補として再利用し、用途別のignoreファイルで不要なテスト・文書・生成物を送信しません。依存関係を変更した場合はキャッシュ効果が下がるため、通常より長くなることがあります。

### 2.2 失敗・手戻りを減らす実行規約

- 本番へ送る内容は、ゲート実行前に一つのcommitへ固定する。未commit差分のままビルドしない。
- 同一commitでrunnerを複数回ビルドしない。公式ラッパーのキャッシュ利用ビルドを一度だけ行い、外側の待機が切れた場合はCloud Buildの終端状態を確認してから、同じ状態ファイルで再開する。
- `-SkipInlineSmokeTest` は、runner/fullで直後に公式07時DryRunを実行する場合だけ使う。設定往復テストと07時DryRunの双方のCloud Run終端条件を確認するまで合格にしない。
- 設定往復テストはrunner/fullで専用Jobだけから明示実行する。Schedulerを追加せず、Cloud Runの再試行は0回とし、失敗時に同じ設定変更を自動で重ねない。
- dashboard-onlyではsettings round-tripを起動せず、`skipped_not_applicable`をstateに残し、23/03/07・02:30・Scheduler・機器settingsが変更されていないことをread-onlyで確認する。
- forecast-onlyではsettings round-tripを起動せず、23/03/07 Job revisionが不変、23/03/07 Schedulerの実効設定が前後一致することをread-onlyで確認する。legacyでは専用検証Job revisionも不変。分離構成の必須SDK proofでは専用検証Jobの設定spec復元を確認する。
- `failed`または`running`の工程を手動で成功へ書き換えない。実機設定が復元済みであることをログから確認した後にのみ、同じ状態ファイルの`-Resume`で未成功工程を再実行する。

### 2.3 再現可能な準備・実server検証・復旧の判定

役割分離版のfixtureは、最終merged commitへ固定し、local/remote HEAD一致・clean worktreeを確認した後に
[正規準備wrapper](../../../scripts/prepare_server_adapter_fixture_from_env.ps1)で生成する。
wrapperは `production_env.ps1` 経由で `.env` を読み、
[local準備本体](../../../scripts/prepare_server_adapter_fixture.py)へ渡す。
prepareはCloud接続・本番書込0で、既存fixtureのコピー改変や上書きを行わない。
既存出力があれば別の明示したprivate出力先を使う。fixtureはUUID、基準時計から732日後の対象日、
source commit、環境identity、計算差分SHA（既承認のSHAとは別途照合）、canonical SHAを束ねる。
token・project/resource ID・raw値はGitignored private artifactだけに保持する。
承認noteは既に受領した承認の参照であり、operator pendingやprepare成功を本番操作の許可へ変換しない。

build前にprepareのcommit/環境identity/計算差分SHAを照合する。一つのmetadata field一致やfixture生成成功は
SDK実証ではない。候補planner digestを固定し、同digestの実server proofでSDK版・fixture SHA・exact execution・
全case・native cleanup/Job復元を確認してからbuildを成功とする。
PowerShell flags/quote/receipt guardやSDK harnessを修正した場合は、関連回帰と正規の全体gateを再実行する。
local fake/CLI helpのPASSを本番SDK PASSへ昇格させない。

SDK専用Jobのexecuteには、既存 `PLAN_SOURCE_REVISION` と `PLAN_IMAGE_DIGEST` を今回のfixture commit/digestで
execution単位に上書きする。`--update-env-vars` は [公式execute仕様](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/execute)
に従い、保存Jobのenvを変更する経路へ置き換えない。receiptの `source_commit` と `runtime_plan_source_revision` は
ともに今回の40hex commitと一致することを必須とする。source/digest/fixture SHAが一致し12caseがPASSでも、
runtime revisionが古ければ全体成功にはしない。local readerもSDK case結果と `runtime_revision_matches` を別表示し、
全体exit0には両方の一致を要求する。

| 判定対象 | 成功の証拠と限界 |
| --- | --- |
| image build | Cloud Buildの終端SUCCESSと固定digest。SDK・本番統合の成功とは別 |
| 実server SDK proof | 同commit/digest/fixtureの全case PASS、receipt SHA/native cleanup/復元一致。source修復・browser・実機の成功とは別 |
| source/派生/history | 各journal receiptとnative readback/source epoch一致。最終verifyの成功とは別 |
| 本番全体 | 必須工程のidentity付き成功、同原本、最終verify、browser、control/readback/restore、backupをすべて要求 |

最新状態の確認は [local状態要約](../../../scripts/summarize_production_validation.py)でformal state・proof・repair journalを
読み、工程ごとの `PASS` / `FAIL` / `NOT_RUN` を分ける。state/receiptが不足・未到達・runningの場合は、
未評価項目の値をUNKNOWNとして扱い、不存在・false・成功を推測しない。
SDKがPASSでもcalculationがFAILなら本番全体はFAILで、未着手のcontrol/web/backupをFAIL実行済みと表現しない。
失敗はimage build、SDK実行、receipt取得/解析、source/派生/history、最終verifyのどこかを明記する。
修復のfailed receiptは `failure_frames` に最後8frameまでのtype/basename/function/lineを保存し、
message・locals・絶対path・秘密を出さない。active exceptionが無い空arrayは原因UNKNOWNであり、原因不存在ではない。
成功receiptとそのSHAは診断追加で変更しない。
後続の値をまだ評価していない場合は、その値をUNKNOWNのまま記録する。

同HEADでCloud BuildやDがrunning/unknownなら、新build・新Dをblindに起動せずexact executionの終端を確認する。
成功工程をskipするにはstateのsuccessに加え、commit/digest/fixture/receipt SHAとnative identityが一致する必要がある。
D採用済みなら固定原本と唯一の採用receiptを保持し、正規Resumeで未完投影/history/verifyだけへ進む。
新HEADへ進む場合は新prepare/fixture/formal stateと通常D一回を所有し、旧HEADのD・承認SHA・採用receiptを流用しない。
失敗stateやunknownを手でsuccessへ書き換えない。

校正のprovider gap、actual/pack/trace不一致、原本不足、未復元device状態は既存STOP条件のまま。
今回利用者が明示承認した[R03の未採用9/20 satellite gap限定例外](../architecture/pv_clear_ghi_new100/release_remediation/steps/R03-derived-history.md)だけは、
修復actual26 SHA・source48/旧35/修復13・採用raw/current・selected pack/原本の必須検証を保持し、
9/20を `journal.stages.verify.calibration.dates` の `pending_satellite_hour_missing` として残して受入を進める。
selected日のgap、retained actualgap、理由不明の未読、不一致は例外対象外で停止する。
pending日と理由を最終報告へ必ず列挙し、衛星値の補完・0埋め・非晴天認定・歴史校正完了の宣言を行わない。
他のstage/実機安全条件は免除せず、未実施deployment工程をPASSへ昇格しない。
未回答の提案・待機中operator判断を許可としてResumeや実機probeを進めない。

## 3. Windowsで途中終了した場合の再開

まず元のstateを読み、exact Cloud Build/Cloud Run executionの終端・設定復元をread-only確認します。`status=complete`は全工程完了の判定であり、中断stateを再開する前提ではありません。`failed`、`running`、`skipped_manual`は成功扱いにしません。dashboard/forecast scopeの`settings_roundtrip=skipped_not_applicable`だけは、そのscopeの正規の非該当状態です。

元のcommit/layout/scope/状態ファイルを保持して再開します。`-Resume`はJSON内の明示的な`success`工程だけをスキップし、Cloud側の現在状態から成功を推測しません。0節と2.3節のidentity/receipt照合も必要です。元の明示scopeがdashboardなら、再開でも同じscopeを指定します。

分離全体反映では、開始時の`-ApprovedCalculationPatchSha256`、`-AdapterProofFixturePath`、`-MonitoringRepairStatePath`、`-ReviewedRepairStateSha256`などの承認・fixture引数も保持する。state pathだけでこれらの入力が自動復元されると仮定しない。開始時の正規コマンドへ`-Resume`を追加し、同じ`-StatePath`を明示する。時間外操作の承認は今回の再開にも適用できることを確認し、旧セッションの一回限り承認を流用しない。

画面限定の再開例:

```powershell
pwsh -NoProfile -File scripts/deploy_production_from_env.ps1 `
  -DeploymentScope dashboard `
  -Resume `
  -StatePath artifacts/deployment_state/production-<開始時刻>.json
```

`WORKING/QUEUED`又は非終端のexecutionがある間は再開せず、監視を続けます。Cloud側だけで成功を確認した未記録工程も、stateを手動成功化したりblindに再実行したりしません。既存の正規復旧・照合手順で解消できない場合はその境界で停止し、最小修正をレビュー・検証します。

`-SkipJobBuild`、`-SkipDashboardBuild`、`-SkipJobDeploy`、`-SkipJob23Deploy`、`-SkipJob03Deploy`、`-SkipJob07Deploy`、`-SkipKpNetImport`、`-SkipDriveBackup`を手動で組み合わせる旧例は使用禁止です。これらの既存flagを通常再開の入口にしません。wrapper内部のscope選択・success判定は維持します。

## 4. 本番反映の合格条件

### 予測Schedulerの認可修復

02:30 forecast SchedulerがHTTP 403で失敗し、予測Job本体の手動実行が成功する場合は、
Scheduler用service accountの`roles/run.invoker`欠損を疑う。認可状態の検証と修復には、
`.env`を直接展開せず次の専用ラッパーだけを使用する。

```powershell
pwsh -NoProfile -File scripts/repair_forecast_scheduler_access_from_env.ps1 -ValidateOnly
pwsh -NoProfile -File scripts/repair_forecast_scheduler_access_from_env.ps1
```

修復後は`-ValidateOnly`を再実行し、Schedulerを1回実行してCloud Run executionの
`Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True`と
failed task 0を確認する。この修復は23/03/07 Job、機器settings、control collectionを変更しない。

### runner / full

次のすべてを確認します。

- 23時、03時、07時の3ジョブが意図した最新イメージへ更新されている
- 変更した非機密環境変数が対象ジョブへ反映されている
- SchedulerがAsia/Tokyoで有効
- Scheduler時刻が23時=`0 23 * * *`、03時=`0 3 * * *`、07時=`0 7 * * *`
- Secret値を出力していない
- settings round-tripが成功し、元settingsへの復元read-backが成功している
- DryRunが完了している
- Cloud Run smokeの最新executionで `Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True` を確認している

DryRunは公式ラッパーで実行します。

```powershell
pwsh -NoProfile -File scripts/run_cloud_job_from_env.ps1 -Slot 07 -DryRun
```

非同期実行の受付だけで成功扱いにせず、最新executionの `Completed=True`、`ResourcesAvailable=True`、`Started=True`、`ContainerReady=True` を確認します。読み取り専用の診断で識別子を取得しても、報告やチャットには記載しません。

### forecast-only

次のすべてを確認します。

- stateの `jobs.status=success`
- stateの `settings_roundtrip.status=skipped_not_applicable`
- `solar-forecast-daily`だけが今回buildしたrunner imageへ更新されている
- 23/03/07 Job revisionがdeploy前後で不変。settings-roundtrip Jobはlegacyではrevision不変、分離構成のSDK proofでは設定specの復元一致
- forecast Schedulerを明示skipした場合、そのschedule/time-zone/targetが不変
- 23/03/07 Schedulerはdeploy前後のschedule/time-zone/targetが一致し、実効変更0
- 機器settings/device stateを変更していない
- Secret値を出力していない

### dashboard-only

次のすべてを確認します。

- stateの `dashboard.status=success`
- stateの `settings_roundtrip.status=skipped_not_applicable`
- dashboard Cloud Run service が今回のcommitからbuildしたrevisionを提供している
- 23/03/07、02:30 forecast job、Schedulerの設定・revisionがこのdeployで変更されていない
- 機器settings/device stateを変更していない
- production APIで対象期間のforecast/actual/provenanceが期待どおり返る
- browserで履歴forecast-vs-actualと予想SOCが表示される
- Secret値を出力していない

## 5. commit / push前

デプロイ中にラッパーを修正した場合は、関連テストだけでなく全体回帰を再実行します。

```powershell
python -m pytest
python scripts/security_check.py
git diff --check
git check-ignore .env
git status --short
```

本番反映内容とコミット内容が一致していることを確認してから、対象ファイルだけをstage、commit、pushします。push後はローカルHEADとremote追跡HEADの一致、およびcleanな作業ツリーを確認します。

## 禁止事項

- ValidateOnlyやテストを実施せずにデプロイする
- 成功ログのない工程を推測でスキップする
- credential-bearing commandを手組みする
- `.env`、Secret値、project番号、resource IDをtracked fileや報告へ記載する
- DryRunの受付だけで本番検証成功と判断する
- 失敗したビルドや状態不明のイメージをジョブへ反映する
- 状態ファイルの `running` または `failed` を手動で `success` に書き換える
- dashboard/forecastの非制御scopeでsettings round-tripや23/03/07の手動実行を追加して「検証」を代替する
