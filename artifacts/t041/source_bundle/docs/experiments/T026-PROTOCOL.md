# T026 事前実験条件

2026-11-10、実装前登録。契約: [T026](../tasks/T026.md)。結果を見て曲線・条件・seedを変えない(望む結果に合わせた調整をしない)。

## 固定する条件

seed100、Python 3.13.3(uv)。7条件(`T011_ALL_CONDITIONS`)×fixed/sampled=14世界、年度長 L ∈ {21,42,84}、126日(T023〜T025と同じ)。開始年齢 S ∈ {18, 29}(任意の値。18は成熟期のみ、29は初回から衰え期に入るための選択)。**84セル**(14×3×2)。曲線あり=`replace(config, season_length_days=L, aging_start_age=S, aging_curve="hypothesis_v1")`、対照=`replace(config, season_length_days=L)`(年齢なし。Sに依らないので42対照を共有)。

## 定義

- `aging_curve`: 文字列、既定`"none"`。許容は`"none"`と`"hypothesis_v1"`。それ以外・非文字列・空はValueError。`"none"`以外で`aging_start_age==0`はValueError。
- 曲線(未校正の仮説、年齢aは加齢後の到達年齢): `delta(a) = +0.02 (a<=24)`、`0 (25<=a<=29)`、`-0.01*(a-29) (a>=30)`。
- 処理: `season_aged`記録の直後、全選手(ID昇順)の`traits`を、各特性 `round6(clamp(old+delta(a), 0.15, cap))` へ更新し、イベント`{"kind":"season_skill_aged","actor":"league","day":その日,"data":{"season":k,"delta":delta(a),"traits":{player_id昇順:{"before":[pace,passing,shooting],"after":[...]}}},"cause_refs":[season_agedのID]}`を1件追加する。cap・fatigue・rates・認識(mind)・乱数・口座は変えない。
- snapshot: 有効時のみconfigに`aging_curve`が増える(`season_skill_aged`はeventsに入る)。
- 不変条件: `aging_curve=="none"`なら`season_skill_aged`はない。有効なら`season_aged`ごとにちょうど直後に1件、season・delta・各選手のafter(=上式をbeforeに適用)・cause_refsが規則と一致する。

## 成功条件(ゲート)

1. 既定不変: 既定14条件の126日digest(identity除く)がT023基準と一致、35日がT018と一致。既定のsnapshotのconfigに`aging_curve`、eventsに`season_skill_aged`がない。`aging_start_age=18`で`aging_curve="none"`の14世界(L=42)は`experiments.t025.check_cell`(加齢なしworldとの完全一致)を通り、`season_skill_aged`を持たない。
2. 規則の忠実性: 84セルで、`season_skill_aged`が`126//L`件、各`delta`が上の式(実験側に別途リテラルで書いた表と一致)、各選手・各特性の`after`が実験側で独立に計算した`clamp(before+delta,0.15,cap)`のround6と一致。手計算の期待(S=29、L=42: 日42/84/126で到達年齢30/31/32、delta -0.01/-0.02/-0.03。S=18、L=21: 到達年齢19..24でdelta全て+0.02)に一致。クランプ(上限cap・下限0.15)にかかった特性数をセルごとに記録。
3. 隔離: (a) 各セルで、曲線ありとなしの対照は、最初の年度末の日(L)より前のイベント列が完全一致。(b) 任意の途中状態(S=29、L=42の42日目の直前)で`_age_players`を呼ぶ前後のpayloadを比べ、差が`players[*].traits`・`events`(+2件)・`player_ages`に限られる(cap・fatigue・rates・mind・coach・ledger・乱数関係は不変)。
4. Policyが真の効果を見ない: AST検査で`player_ages`と`aging_curve`の参照が、決定関数(`decide_*`)・`policy.py`・`observation.py`・`league_observation.py`・`advice.py`・`standings.py`に一つもなく、`league_economy.py`内の参照関数が許可リスト(config、初期world構築、`_age_players`、不変条件、snapshot出力、trusted reader)に限られる。
5. 再演と保存: 84セルを保存し、全再演reader・信頼readerで読んだworldが`_assert_same`で一致。介入1件を挟んだ分岐world(S=29、L=42、30日→fork→+60日)も全再演で一致。ステップ実行(各年度末ごとに`advance_league_days`)と一括実行のsnapshotが1セルで一致。
6. 改ざん検出(S=29、L=42、126日の1world): `season_skill_aged`の削除・`delta`変更・`after`変更・snapshot側の選手traits変更は全再演readerが拒否する。信頼readerは削除・delta変更・after変更を拒否する。信頼readerでsnapshotの選手traits変更(イベントと矛盾しない側の改ざん)を受理する場合は、既知の限界(T022)として特性評価に記録し、合否外。
7. 不正値と検査: `aging_curve`が未知文字列・int・空文字、および`aging_start_age=0`との組合せはValueError。全unittest合格(3.13.3)、旧成果物inventory(`artifacts/t026/preflight/historical-inventory.json`)のhash不変、source_bundle保存。

## 予測(合否外、支持/反証として記録)

結果を見る前に固定。ゲートではなく、曲線が期待どおり働いたかの観察。
- P1: S=18の42セルで、最終日(126日)の全選手平均composite skillが対照より高いセルが36以上。
- P2: S=29の42セルで、同じく対照より低いセルが36以上。
- P3: クランプにかかった特性数の割合が、S=18でも29でも全体の20%以下(曲線がcapに潰されていない)。超えた群は「capで制限された」と書く。
- 記述のみ(予測なし): 最終日の平均composite差を、曲線の名目累積(Σdelta)で割った比(日々の訓練が回復/抑制する分)、年度末ごとの平均composite、移籍イベント件数の曲線あり−対照の差。

## 失敗条件

1〜7のいずれかの不一致、既定の履歴・snapshotの変化、凍結成果物の変更、結果後の規則(曲線・S・L・予測の閾値)の変更。P1〜P3の反証は失敗ではなく結果として記録する。

## 限界

曲線の数値は未校正の仮説で、実データ由来ではない。全選手に同じdeltaを与える(個人差・特性差・成長率との相互作用なし)。年度長・開始年齢は任意。Policyは年齢を見ないが、真の技能は変わるのでノイズ付き観測を通じて間接に影響を受ける。引退・新人・年齢分布は未実装。
