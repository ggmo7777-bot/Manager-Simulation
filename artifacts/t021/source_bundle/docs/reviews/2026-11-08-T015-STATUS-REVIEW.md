# T015 現状確認 — 2026-11-08

確認担当: Codex。対象は現在の作業ツリーと保存済みの結果。シミュレーションの実行規則は変更せず、T015の採用・凍結も行わない。

## 現在地

現行コードはT015、save16 / rules `t015-sampled-population-v1`。固定プロフィールに加え、初期プロフィールを範囲から生成する`sampled`を持つ。既定は`fixed`。T014はsave15の凍結済み比較基準であり、現行コードと混同しない。

4クラブ・4監督・12選手、A/B学習、日予算、2対2、選考、合意移籍、整数会計、記録付き認識介入まで。加齢、引退、新人、毎季の移籍市場、C、3Dは未実装。

## 修正・整理が必要な点

### 1. 「残留側成長」の集計が実際の残留者を表さない

`soccer_sim/league_economy.py:2699`の`academy_retained_pids`は`p1,p2,p7,p8`に固定されている。T015ではこの4名も移籍する一方、`p3,p9`が育成クラブに残ることもある。`talents_*`をスロット指標とする注意書きだけでは、この別の指標の意味のずれを解消できない。

例: sampled / open_solidarity / seed100 / 84日では、p1はclub_bへ移籍済みだが集計に入り、育成クラブに残るp3,p9は入らない。したがって既存の+0.006886は固定4スロットの平均成長差として解釈する。「残留者への効果が固定fixtureの約30%」という解釈は保留する。これはシミュレーションの取引・成長値の破損ではなく、評価対象と結論の問題である。

再演90本のsampledのうち、市場開放・還元なし30本中27本、市場開放・還元あり30本中27本で、固定4名の集計に移籍済み選手が入っていた（各条件で43選手×run）。fixedの90本ではこの混入は0だった。固定fixtureで成立したスロットの意味を生成集団へそのまま持ち越したことが原因である。

次の最小修正は評価対象の定義。初期育成6名を固定した比較と、実際の残留者についての記述統計を分ける方法が候補。単に条件ごとの残留者へ置き換えると、移籍自体が制度の影響を受けるため比較対象が変わる。旧結果は上書きせず、定義を明記した別集計を作る。

### 2. sampledは通常のrun CLIに未接続

`soccer_sim/__main__.py:38`のchoicesは既存条件辞書だけで、`soccer_sim/experiment_t011.py:27`にもsampled接尾辞がない。`python3 -B -m soccer_sim run --condition league_open_solidarity_sampled_population --seed 100 --days 1 --out <新規出力先>`はexit 2で拒否される。Python APIと`experiments/t015.py`による実行は可能。CLI接続を完成済みと扱わない。

### 3. 資料の版が混在していた

確認開始時のREADMEは冒頭T015/本文save15/168テスト/分布生成未実装、SOL-HANDOFFはsave14/次は整数化、STATUSの「今回の変更」はT013中心だった。今回、README・STATUS・引継ぎと外部指摘表を現行コードに合わせる。凍結source_bundle内の過去資料は変更しない。

## 検証と証拠

- 現行の全unittest: 175件合格、94.784秒（`python3 -B -m unittest discover -s tests -v`）。
- T014 inventoryの252ファイル、source_bundle manifestの123ファイル、それ以前のinventoryの1,311ファイルにhash不一致なし。
- T015の保存済みrun1のruntime source hashと現在のコードは一致: `483c47178c7683f125ba53a704f42909972b4201b889a069f2c988402c57a9f3`。
- T014から変わったruntimeファイルは`league_economy.py`のみ。通貨、観測、私的Policy境界の旧修正を巻き戻していない。
- fixedの全7条件×seed100..104×84日、35比較で、T014と全状態・全イベント列が一致。identityと新設Configの`population_mode`だけを比較から除外した。
- fixed/sampled×3制度×30seed、180本を再演し、保存済みの8集計値を全件再現。sampledの元runには全履歴が保存されていないため、180本について旧履歴との一致を確認したとは言わない。
- 再演した180本の107,376取引を、実行側と独立した整数監査で口座・累計・各時点の保存則と照合。3,344要求の解決、624件の配当支払の一意性も照合した。今回の180本には攻撃専用条件を含めていない。

追加の再演・独立会計監査は`experiments/review_t015.py`で実施し、130.824秒で完了。新しい[監査JSON](../../artifacts/local/status-20261108-t015/verified/audit.json)へ記録した。[全テストログ](../../artifacts/local/status-20261108-t015/tests.txt)、[確認manifest](../../artifacts/local/status-20261108-t015/manifest.json)も保持する。旧`artifacts/t015/run1/comparison.json`は変更していない。初回の診断スクリプトは集計辞書を加算する処理で中断し、その処理を直して上記の全比較を再実行した。実行規則の不具合ではない。

再実行: `python3 -B experiments/review_t015.py --out <新規ディレクトリ>`。既存出力先は拒否する。これは今回のsource hash・保存済みrun1に対応する現状監査であり、将来版の比較結果を保証しない。

## 残る限界

T015にはrun1の比較JSONがあるが、source_bundle/manifest/inventoryはまだない。試用であり凍結保証は未成立。生成範囲は未校正で、分布の幅は固定fixtureより狭い。範囲・初期印象・旧p3/p9設定を同時に変えているため、効果差の原因の切り分けは未実施。初期集団の生成だけで世代交代や長期的な自律進化を実装したとは言えない。

source hash範囲の分離、全再演readerの高速化、世界間の重複整理、直書き係数の整理も残る。今回の現状確認を、これら全部への着手指示とは扱わない。
