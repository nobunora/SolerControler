# 時刻別PVベクトル補正の改善方針

## 1. 結論

`HOURLY_VECTOR_CORRECTION_REVIEW.md` の再現実験は、時刻単位で補正候補を扱うことと、補正量を減衰させることには一定の根拠を与えている。一方で、現在の証拠だけから `multiplier` 法を本番採用してはならない。

優先順位は次の通りとする。

1. **P0: 予報vintageを不変保存し、評価入力を信頼できる状態にする。**
2. **P0: 欠損気象特徴量を 0 と同一視せず、データ品質ゲートを追加する。**
3. **P1: 現行production補正を含む walk-forward 比較基準を作る。**
4. **P1: 「天気クラス一致 + 短波±30%」のhard gateを、同一lead time・太陽幾何・NWP特徴量を使う連続距離のanalog検索へ置き換える。**
5. **P1: 過去の絶対誤差[kWh]をそのまま移植せず、季節・発電規模に対して正規化した誤差を推定する。**
6. **P1: 類似度と新しさで重み付けし、effective sample sizeと分散で0補正へshrinkする。**
7. **P2: 不確実性を出力し、MAEだけでなくSOC最適化の実コストで採否を決める。**

目標は「毎回何かを補正すること」ではない。**過去データから有意に情報が得られるときだけ、校正された量だけ補正すること**を目標とする。

---

## 2. 今回確認した現行実装

### 2.1 物理PV予測

`app/forecasting/pv_array.py` は、面別アレイについておおむね次の形でPVを計算している。

```text
PV_i(t)
= capacity_i
  * GTI_i(t) / 1000
  * performance_ratio_i
  * calibration_factor
  * shading_factor_i
  * temperature_factor_i(t)
```

さらに `app/forecasting/pv_array_calibration.py` で過去実績とarchive weatherを用いた日次calibrationを行う。このため、本システムは既に「物理モデル + 統計校正」のhybrid構造である。

### 2.2 本番の時刻別 residual correction

`app/forecasting/correction_model.py::_physical_vector_residual_correction` は、同一時刻について次を満たす過去行を候補にする。

- 過去予測PV > 0
- target / candidate の shortwave > 0
- coarse weather classが一致
- candidate shortwave が target の70%-130%

候補の残差を

```text
residual_i = actual_i - forecast_i
```

とし、その中央値 `center` を求める。その後、本番コードでは既に候補数と分散によるshrinkageを行っている。

```text
weight
= n / (n + 2)
  * spread^2 / (spread^2 + variance)

prediction
= max(0, forecast + weight * center)
```

したがって、レビュー実験の `raw` と本番動作は同一ではない。次回以降のbacktestでは **production-equivalent arm** を必須とする。

### 2.3 予報履歴はvintageを保持していない

現在のSQLite `forecast_hourly` は `(date, hour)` がPRIMARY KEYであり、同一forecast dateの再取込時には、その日の既存行を削除して最新runの行を保存する。

Firestoreも同一forecast dateの既存 `forecast_hourly` documentsを削除し、`YYYY-MM-DD-HH` のdocument idで再作成する。

この設計では、同じtarget hourについて「23:00時点で見えていた予報」「翌朝に更新された予報」などを区別して保存できない。`updated_at` は現在値の更新時刻としては使えるが、過去vintageを復元できない。

さらに `correction_history_io.py` が補正用に読む項目は、PV、load、shortwave、weather codeが中心で、forecast issue time / lead time / provider versionをモデル入力として保持していない。

**結論:** 現在の保存値だけでは、実運用時点と同じ予報情報だけを使った完全なprospective replayを保証できない。

### 2.4 凍結データの気象特徴量に不連続がある

`docs/current/product/evidence/hourly_vector_dataset_2026-08-23.csv` では、予報PVが存在する2026-05-24から2026-06-20まで、日中を含め `shortwave_w_m2=0.0` の行が続き、2026-06-21から非zeroの値が入る。

現行similarityは `target_shortwave > 0` かつ `prior_shortwave > 0` を要求するため、この期間は「似ていない」のではなく **類似判定に必要な特徴量が欠落しているため候補になれない**。

したがって、45日lookbackという設定値と、実際に使える45日分の履歴は同義ではない。

---

## 3. 既発表研究から得られる示唆

### 3.1 Yang & van der Meer (2021): solar forecast post-processing

D. Yang and D. van der Meer, “Post-processing in solar forecasting: Ten overarching thinking tools,” *Renewable and Sustainable Energy Reviews*, 140, 110735, 2021. DOI: https://doi.org/10.1016/j.rser.2021.110735

このreviewは、solar forecastingでpost-processingが標準的な工程になっていることを整理し、deterministic forecastに対して regression、filtering、analog ensembleなどを体系化している。

**本システムへの示唆:** 物理予測を捨てて巨大なend-to-end MLへ移行する必要はない。現行の物理予測を維持し、誤差だけを局所的・適応的に校正する構造は妥当である。

### 3.2 Alessandrini et al. (2015): Analog Ensemble

S. Alessandrini, L. Delle Monache, S. Sperati, and G. Cervone, “An analog ensemble for short-term probabilistic solar power forecast,” *Applied Energy*, 157, 95-110, 2015. DOI: https://doi.org/10.1016/j.apenergy.2015.08.011

この研究では、forecast lead timeとlocationを揃えた上で、過去のdeterministic NWP forecastから現在予報に似たanalogを検索し、それに対応する実測solar powerを利用する。太陽高度・太陽方位などの天文要因と、cloud coverなどの気象要因がsolar power予測に重要であることも明示されている。

**本システムへの示唆:** 「同じ時計時刻」「粗いweather class」「shortwave±30%」だけでは情報を捨て過ぎる。少なくともlead timeを一致させ、太陽幾何と連続的なNWP特徴量で距離を定義する方が、analog forecastingの考え方に整合する。

### 3.3 Nguyen & Müsgens (2022): 180論文のメタ分析

T. N. Nguyen and F. Müsgens, “What drives the accuracy of PV output forecasts?,” *Applied Energy*, 323, 119603, 2022. DOI: https://doi.org/10.1016/j.apenergy.2022.119603

180論文・1136 error observationsを分析し、hybrid models、data processing、data normalization、clear-sky index、NWP variablesの利用が予測精度と関連することを報告している。また、短いtest setによる性能の歪みを避けるため、少なくとも約1年のtest periodを推奨している。

**本システムへの示唆:** 絶対kWh残差を季節をまたいで移植するより、太陽条件や発電規模で正規化した誤差を扱うべきである。また、現在の約3か月のデータだけで季節一般化を主張してはならない。

### 3.4 Pelland, Galanis & Kallos (2013): Kalman bias removal

S. Pelland, G. Galanis, and G. Kallos, “Solar and photovoltaic forecasting through post-processing of the Global Environmental Multiscale numerical weather prediction model,” *Progress in Photovoltaics*, 21(3), 284-296, 2013. DOI: https://doi.org/10.1002/pip.1180

0-48h aheadのhourly solar/PV forecastに対して、spatial averagingとKalman filterによるbias removalを適用した。1年をtraining、その翌年をtestingに使い、post-processingなしのGEM forecastに対して平均RMSEを改善した。

**本システムへの示唆:** 最近の誤差からbiasを逐次更新するfilteringは有効な候補である。ただし、本システムではまずanalog similarityと予報vintageを正しくした上で、EWMA/Kalman相当の時間適応を比較する。

### 3.5 Theocharides et al. (2020): data quality + weather clustering + regression

S. Theocharides et al., “Day-ahead photovoltaic power production forecasting methodology based on machine learning and statistical post-processing,” *Applied Energy*, 268, 115023, 2020. DOI: https://doi.org/10.1016/j.apenergy.2020.115023

方法論にdata-quality stage、weather clustering、linear-regression post-processingを明示的に含め、solar irradiance biasを後処理で補正している。

**本システムへの示唆:** 今回見つかったshortwaveの0埋め問題は、モデル選択より先に扱うべきである。欠損・無効値をmeteorological zeroと区別するdata-quality stageを設ける。

### 3.6 Liu et al. (2026): concept driftとonline error correction

H. Liu, H. Wu, H. Jin, and Y. He, “Adaptive forecasting of photovoltaic power based on dual-type models’ ensemble and online error correction,” *Applied Energy*, 408, 127397, 2026. DOI: https://doi.org/10.1016/j.apenergy.2026.127397

PV seriesの分布が時間とともに変わるconcept driftを対象に、online error correctionとadaptive aggregationを組み合わせている。

**本システムへの示唆:** `HOURLY_VECTOR_CORRECTION_REVIEW.md` で全期間は改善する一方、8月・直近14日で悪化した事実は、固定45日窓の静的ルールだけでは最近のregime changeへ追随できない可能性と整合する。ただし、現時点のローカルデータ量では同論文級の複雑なensemble/deep learningを直接導入しない。まずrecency weightingと簡潔なonline bias stateで十分かを検証する。

---

## 4. 問題を4層に分離する

現在の「予測と実績が乖離する」を、1つの補正係数で解決しようとしてはいけない。原因を次の4層に分離する。

### Layer A: forecast provenance / data quality

- operational issue timeと保存されたforecastが一致しているか
- forecast provider / model versionが途中で変わっていないか
- shortwave / cloud cover等が欠損していないか
- `0` が真の0か欠損代替か
- timezone / hour boundaryが一致しているか

### Layer B: physical/base model bias

- array capacity / orientation / shading / PRのずれ
- provider irradiance bias
- temperature correctionのずれ
- calibration layerの過補正・二重補正

### Layer C: conditional residual bias

- 同じようなNWP状況で繰り返す残差
- 朝/昼/夕、solar elevation、cloud regimeごとのbias

ここだけをhourly analog correctionで扱う。

### Layer D: non-stationarity

- 季節遷移
- パネル温度・影・周辺環境の変化
- provider/model更新
- 設備状態変化

ここはrecency weighting / online filter / drift monitoringで扱う。

---

## 5. P0: 最初に直すデータ契約

### 5.1 append-only forecast snapshotを追加する

既存 `forecast_hourly` は互換性のため「latest forecast」として残してよい。別にappend-onlyの履歴を持つ。

例:

```text
forecast_hourly_snapshots
- forecast_run_id
- issued_at_utc
- issued_at_local
- target_date
- target_hour
- lead_minutes
- forecast_pv_kwh
- forecast_load_kwh
- forecast_shortwave_radiation_w_m2
- forecast_cloud_cover
- forecast_precipitation_probability
- forecast_temp_c
- forecast_relative_humidity_percent
- forecast_dew_point_c
- forecast_wind_speed_10m
- forecast_weather_code
- pv_provider
- weather_provider
- model_version / code_commit
- feature_quality_flags
```

主キー/document idは `(forecast_run_id, target_date, target_hour)` 相当とし、既存runを後からoverwriteしない。

### 5.2 correction modelはlead timeを混ぜない

23:00に翌日を決めるforecastと、翌朝更新されたforecastは別populationとして扱う。

最低限、analog candidateは次を満たすこと。

```text
abs(candidate.lead_minutes - target.lead_minutes) <= lead_tolerance
```

可能なら運用slotごとに分離する。

### 5.3 無効なweather featureを0にしない

次のような行は `invalid_feature` として扱う。

```text
forecast_pv_kwh > daylight_floor
AND shortwave_radiation_w_m2 <= 0
```

実際の日の出・日没近傍を除き、PV予報が正なのにshortwaveが0の場合は「快晴0 W/m2」と解釈しない。

backtest出力には少なくとも以下を含める。

- requested lookback days
- available forecast days
- valid-feature days
- candidate rows before/after quality gate
- missing/invalid ratio by feature
- earliest valid feature date
- provider/model-version discontinuity

---

## 6. P1: analog検索をhard gateから連続距離へ変更する

### 6.1 現行方式

```text
same hour
AND same weather class
AND 0.7 * target_shortwave <= prior_shortwave <= 1.3 * target_shortwave
```

この方式は境界で候補が突然0/1になり、`clear` という粗いclassの中の雲量差を表現できない。

### 6.2 提案方式

候補の最低条件は、

- strictly historical
- same operational forecast vintage / compatible lead time
- valid feature quality
- daylight / PV-eligible

とし、その後は連続距離で上位analogを選ぶ。

初期feature候補は、既に `forecast_hourly` に保存されている値を優先する。

```text
x = [
  solar_geometry,
  normalized_shortwave,
  cloud_cover,
  precipitation_probability,
  forecast_pv_scale,
  temperature,
]
```

`weather_code` は補助特徴として使ってもよいが、粗いclassの完全一致を必須条件にしない。

#### solar geometry

固定clock hourだけでは季節変化を吸収できない。solar elevation / azimuth、またはそれと等価なday-of-year + local solar time特徴を追加する。

#### normalized shortwave / clear-sky index

clear-sky irradianceが利用可能になれば、

```text
k_clear = forecast_shortwave / clear_sky_shortwave
```

を第一候補とする。

clear-sky値をまだ安全に生成できない段階では、新依存を急いで追加せず、まず保存済みNWP変数とsolar geometryの標準化距離で比較実験する。

### 6.3 距離

最初は説明可能なstandardized Euclidean distanceを使う。

```text
d_i^2 = Σ_j omega_j * ((x_target,j - x_i,j) / scale_j)^2
```

`scale_j` はtraining historyだけから得たMAD/IQRなどのrobust scaleとする。

固定weightを恣意的に本番投入しない。少数の事前定義候補をrolling-origin validationで比較する。

### 6.4 top-k + distance cap

±30%の1本の閾値ではなく、

1. 距離の小さい順に並べる
2. 最大 `k_max` 件まで選ぶ
3. `distance_cap` を超えるものは除く
4. effective sampleが不足すれば補正を0へshrink / fallback

とする。

---

## 7. P1: 絶対kWh残差の移植をやめる

45日前の `+0.30 kWh` が今日も同じ意味とは限らない。solar geometryや日射強度が変われば、同じ相対biasでも絶対kWhは変わる。

次の3方式を **同じwalk-forward split** で比較し、事前に1方式へ決め打ちしない。

### Candidate A: 現行 additive residual

```text
e = actual - forecast
```

比較基準として残す。

### Candidate B: multiplicative / log residual

```text
r = log((actual + eps) / (forecast + eps))
```

推定後:

```text
prediction = forecast * exp(shrink * r_hat)
```

低出力時はratioが不安定になるため、daylight / minimum forecast gateまたは別fallbackが必要。

### Candidate C: clear-sky normalized additive residual

clear-sky PV scaleを信頼できる形で得られる場合:

```text
r = (actual - forecast) / max(clear_sky_pv, eps)
prediction = forecast + shrink * r_hat * clear_sky_pv_target
```

文献との整合と季節scale耐性の観点では有力だが、clear-sky scale自体の品質を先に検証する。

---

## 8. P1: recency + similarity weighting + robust shrinkage

最近のregimeへ追随しつつ、1-2件の偶然で補正を暴れさせない。

候補 `i` の重みを次の形で持つ。

```text
w_i
= exp(-age_days_i / tau_time)
  * exp(-distance_i^2 / (2 * tau_distance^2))
```

誤差中心はweighted medianまたはHuber locationを第一候補とする。

```text
r_hat = robust_weighted_location(r_i, w_i)
```

有効標本数:

```text
n_eff = (Σw_i)^2 / Σ(w_i^2)
```

本番に既にある考え方を拡張し、補正量を0へshrinkする。

```text
count_term = n_eff / (n_eff + kappa)
variance_term = spread^2 / (spread^2 + local_variance)
shrink = count_term * variance_term
```

最終的には、

```text
correction = shrink * local_bias_estimate
```

とする。

`tau_time`, `tau_distance`, `kappa`, `spread` は同一期間で最適化して同一期間を評価してはならない。rolling-origin / nested time-series validationで決める。

### 8.1 full reversalは初期候補から外す

現行reviewのgenuine-hit replayでは `-1` が一度も選ばれず、改善の中心は `0.5` や `1.0`、および一部 `0` だった。

したがって、初期production候補では「反転するか」ではなく「どの程度信頼して適用するか」に重点を置く。負の係数は、将来の十分なout-of-sample evidenceが得られるまで本番仕様にしない。

---

## 9. P1: 必須backtest設計

### 9.1 rolling-origin only

各target `(date, hour)` の計算では、そのforecast issue時点より前に利用可能だった情報だけを使う。

```text
train/history  -> target forecast -> freeze prediction -> later actual
```

future actual、future forecast revision、target outcomeをhyperparameter selectionへ入れない。

### 9.2 比較arm

最低限、同一sample setで以下を比較する。

1. physical/base forecast only
2. **current production `_physical_vector_residual_correction` equivalent**
3. review raw additive
4. review multiplier
5. proposed continuous-analog + recency + robust shrink
6. proposed normalized residual variant(s)
7. simple EWMA/Kalman-style bias filter

### 9.3 stratification

全体平均だけで結論を出さない。

- month / rolling 7d / 14d / 30d
- hour
- solar elevation bucket
- clear/cloud/rain regime
- forecast magnitude bucket
- lead-time bucket
- provider/model version
- data-quality status

### 9.4 metrics

最低限:

- MAE [kWh]
- normalized MAE
- RMSE
- signed bias / mean error
- median absolute error
- hit / applicable coverage
- effective sample size
- correction magnitude distribution
- fallback ratio

SOC最適化につなぐ評価ではさらに:

- grid purchase kWh / yen
- sale / curtailment影響
- target SOC deviation
- battery constraint violationの有無
- baselineに対するdownstream cost difference

誤差のhourly samplesを独立標本と仮定しない。日単位block bootstrapなどでpaired uncertaintyを評価する。

---

## 10. P2: point correctionからuncertainty-aware forecastへ

Analog Ensembleの考え方を利用すると、analog residualの分布そのものが不確実性情報になる。

初期段階では複雑なprobabilistic modelを導入せず、次を保存するだけでもよい。

- p10 / p50 / p90 residual
- local MAD / variance
- `n_eff`
- analog distance distribution
- estimated correction confidence

SOC optimizer側で、不確実性が高い時間帯に過度に楽観的なPVを前提としないシナリオを作れる。

---

## 11. 実装順序

### Phase 0 — evaluation integrity

変更対象候補:

- `app/operations/sqlite.py`
- `app/operations/firestore.py`
- `app/forecasting/correction_history_io.py`
- DB migration / backend compatibility tests

実施内容:

1. append-only forecast snapshots追加
2. issue time / lead time / provider/model provenance保存
3. weather feature quality flags追加
4. snapshot読込API追加
5. backtestはsnapshotだけを使用可能にする
6. 既存 `forecast_hourly` はlatest compatibility view/tableとして維持

**Phase 0が完了するまで、補正方式の本番優劣を確定しない。**

### Phase 1 — production-equivalent benchmark

変更対象候補:

- `scripts/reproduce_hourly_reverse_vector_correction.py` または新しい比較script
- production correction helperのI/O-free化/再利用（必要な場合のみ）

実施内容:

- current production weightingをbacktest armへ追加
- invalid shortwave期間を可視化
- forecast vintage mismatchを検出してfail/skip
- rolling-origin split固定

### Phase 2 — adaptive analog correction

変更対象候補:

- `app/forecasting/correction_model.py`
- 必要なら小さい独立helper module
- focused unit tests

実施内容:

- continuous normalized feature distance
- top-k analog
- recency weighting
- robust residual estimator
- effective sample size
- existing variance shrinkageのweighted版
- structured diagnostics

### Phase 3 — normalized residual comparison

- additive
- log-ratio
- clear-sky normalized

を同じframeworkで比較し、1方式だけ本番候補にする。

### Phase 4 — uncertainty / SOC objective

- analog residual quantiles
- paired PV/load scenarioへの統合
- downstream SOC cost評価

---

## 12. 実装時に維持する契約

- 既存の物理PV予測を置換しない。post-processing layerとして実装する。
- `forecast_hourly` の既存consumerを壊さない。
- 新依存は、stdlib/既存依存で安全に実現できないことを確認するまで追加しない。
- 欠損値を暗黙に0へ変換しない。
- correction failureはbase forecastへfail-safeする。
- future actual / future revised forecastを学習に使わない。
- diagnosticsから、なぜ補正した/しなかったかを再現できるようにする。
- production correctionの有効化はfeature flag下で行う。

---

## 13. production採用条件

### 短期engineering gate

最低30日、できれば季節遷移を含むprospective shadow runを行う。各予報はactual到着前にsnapshotをfreezeする。

採用判定は事前に固定し、少なくとも以下を満たすこと。

1. full shadow periodでbaselineより改善
2. recent 7d / 14d / 30dで重大な劣化を隠していない
3. signed biasが一方向へ悪化していない
4. downstream SOC costが非劣性以上
5. 特定hour / weather regimeだけの改善で全体値を作っていない
6. candidate coverage / fallback / `n_eff` が説明可能
7. data-quality violation時にbase forecastへ安全にfallbackする

非劣性marginは任意のkWh値で決めず、SOC/買電コスト上許容できる差から事前設定する。

### 季節一般化gate

現在の約3か月の履歴だけで年間一般化を主張しない。少なくとも1年規模のout-of-sample評価を継続し、季節をまたいだ結果を別途確認する。

---

## 14. 現時点で採用しないもの

### 14.1 `multiplier` gridの即production化

全期間では改善しても、2026-08-01以降と直近14日ではbaselineより悪い。さらに本番コードには既に別のshrinkageがあり、review scriptの比較だけではproduction置換の根拠にならない。

### 14.2 full sign reversal

現行replayでは主な改善源ではない。

### 14.3 大規模deep learningの即導入

データ量、forecast vintage、特徴量品質が未確定の状態でモデル容量だけ増やすと、原因切り分けと再現性を悪化させる。

### 14.4 MAE全期間値だけによる採用

今回すでに「全期間改善・直近悪化」が発生しているため禁止する。

---

## 15. 最初の実装PRに要求するacceptance criteria

最初のコードPRは **Phase 0のみ** とし、予測アルゴリズムを変更しない。

- [ ] forecast snapshotがappend-onlyで保存される
- [ ] 同一target `(date,hour)` に複数vintageを保持できる
- [ ] `issued_at` と `lead_minutes` が保存される
- [ ] provider/model provenanceを保存できる
- [ ] invalid shortwave等のfeature qualityを識別できる
- [ ] existing `forecast_hourly` latest behaviorは互換維持される
- [ ] snapshot readerがtarget issue timeより後のforecastを返さない
- [ ] SQLite / Firestore双方にfocused testがある
- [ ] backtestで使用したvintageを結果JSONに記録できる
- [ ] credentials / project identifiersをtracked artifactへ出さない

Phase 0の実データが一定量蓄積してから、Phase 1/2を別PRで評価する。

---

## 16. 最終判断

現在の乖離に対する改善の中心は、**「逆向き補正」ではなく「正しいvintageの、品質が保証されたNWP特徴量を使って類似度を連続的に測り、季節scaleを正規化した残差を、最近性と不確実性に応じてshrinkして適用する」こと**とする。

現行の物理PVモデルは残し、hourly correctionを軽量なadaptive post-processing層として改善する。これが既発表研究との整合、現在のデータ量、既存コードの変更量、説明可能性のバランスが最も良い方向である。
