# T027 事前実験条件

2026-11-10、実装前登録。契約: [T027](../tasks/T027.md)。結果を見て特性・範囲・閾値・seed・条件を変えない。失敗は失敗として記録し、望む結果に合わせた調整をしない。

## 固定する条件

Python 3.13.3(uv)。14世界(7条件`T011_ALL_CONDITIONS`×fixed/sampled)、126日。seed ∈ {100,101,102}(基本)。分布検査のseed集合は 0..999。

## 定義

- `LeagueEconomyConfig.personality`: 文字列、既定`"none"`、許容は`"none"`と`"latent_v1"`。それ以外・非文字列・空はValueError。`"none"`以外のときのみ`to_dict`にキーを出す(既定は出さない)。独立に使える(`season_length_days`・`aging_*`と無関係)。
- 特性: `emotional_stability`、`discipline`。値は`round6(uniform_from_key(seed, 0, "personality:<特性名>", 選手ID, low=0.15, high=0.85))`。選手IDは`players`の全員(fixed/sampled共通でp1..p12)。
- `LeagueWorldState.player_personality`: `{選手ID昇順: {"discipline": v, "emotional_stability": v}}`(キーは特性名昇順)。有効時のみsnapshotに`player_personality`キーが入る。進行中は一切変わらない。
- 不変条件: `personality=="none"`なら空。それ以外は`set(player_personality)==set(players)`、各行のキー集合が2特性、各値が型float・[0.15,0.85]・seedと選手IDから再計算した値と`==`。
- 何にも使わない: 決定関数・成長式・観測・イベントに現れない。

## 成功条件(ゲート)

1. **既定不変**: 既定14条件の126日digest(identity除く)がT023基準と一致、35日がT018と一致(`experiments.t019.check_regression`)。既定のsnapshotのconfigに`personality`、worldに`player_personality`がない。`personality="none"`を明示した世界も既定とconfig・snapshotが同一。
2. **履歴不変(有効化の影響なし)**: (a) 14世界×seed{100,101,102}で、有効化した世界のsnapshot(events・interventions・players・minds・coaches・台帳・口座を含む全体)から`config.personality`と`player_personality`だけを除くと、無効の世界と完全一致、`summarize_league_world`も一致、イベント数も同一。(b) 14世界(seed100)で`season_length_days=42`・`aging_start_age=29`・`aging_curve="hypothesis_v1"`を併用しても同じ(除去後に、併用で無効の世界と完全一致)。
3. **漏れ検査(特性が判断に入らない)**: (a) 動的: 14世界(seed100)で、特性を全員0.15、全員0.85、に差し替えた世界(構築直後に`player_personality`を書換え、以降は進行のみ)を126日進め、生成値の世界と、`player_personality`を除き履歴が完全一致する(特性値を変えても履歴が変わらない)。(b) 静的: `soccer_sim`の`league_economy.py`以外の全moduleに`personality`・`emotional_stability`・`discipline`の文字列がない。`league_economy.py`で`player_personality`・`personality`(属性)・`_personality_traits`・`PERSONALITY_*`を参照する関数は許可リスト(`__post_init__`、`to_dict`、`has_save_fields`、`_build_initial_world`、`_validate_league_invariants`、`_snapshot_payload_unchecked`、`load_league_snapshot_trusted`、`_personality_traits`)に限られ、`decide_*`・`_step_*`・成長/移籍/選考の関数に一つもない。許可外の参照を意図的に足した合成コードを同じ検査が検出する(検査の感度)。
4. **生成器の忠実性**: (a) 実験側に別途書いた独立実装(JSON→sha256→上位53bit)で、14世界×seed3種の全選手・2特性の値が実装と完全一致。(b) seed 0..999×12選手×2特性=24,000値(sampled/fixedは同じIDなので世代器を直接)で: 全値が[0.15,0.85]、各特性の平均が0.50±0.01、標準偏差が0.2021±0.006(一様の理論値0.7/√12)、一様分布とのKS統計量が1.63/√12000(=0.0149、1%水準)以下、2特性の相関の絶対値<0.03、各特性とsampledの既存`ambition`・`loyalty`・`autonomy`との相関の絶対値<0.03(seed0..999のsampled人口で、academy/buyer含む全選手)、academyとbuyerのクラブ間平均差の絶対値<0.02、選手IDごとの平均(seed方向)の0.50からの差<0.03、同じ選手IDのseed100と101の値が全選手で異なる(IDだけで決まらない)。(c) 既存draw不変: `_sample_profiles`/`_sample_impressions`の出力が、`personality`有効・無効のconfig下で同一(seed 0..99、全14世界ではなくsampled人口で)。
5. **再演と保存**: 有効14世界(seed100、126日)を保存し、全再演reader・信頼readerで読んだworldが`_assert_same`で一致、manifest付き。介入1件を挟んだ分岐world(30日→fork→+60日)で`player_personality`が親と同一で、全再演readerで一致。ステップ実行(30日+残り)と一括実行が一致。旧形式(キー無し)の既定snapshot14世界を新readerが全再演・信頼の両方で読める。`has_save_fields`が有り/無しの両configを受理し、未知キーは拒否する。
6. **改ざん検出**(1世界、sampled、126日): snapshotの(i)特性値変更、(ii)選手の削除、(iii)特性キー削除、(iv)余分な特性キー追加、(v)範囲外値(0.9)、(vi)configが`none`なのに`player_personality`を追加、(vii)configが有効なのに`player_personality`を削除、を全再演readerは全て拒否する。信頼readerは、manifestの`snapshot_sha256`を改ざん後のsnapshotに合わせて再計算してもなお(i)〜(vii)を全て拒否する(不変条件による。T022のイベント削除などの限界とは別に、本taskの値は再計算可能なため)。
7. **不正値と検査**: `personality`が未知文字列・int・空文字ならValueError。全unittest合格(3.13.3)、旧成果物inventory(`artifacts/t027/preflight/historical-inventory.json`)のhash不変、source_bundle保存。

## 記述のみ(合否外)

特性ごとの標本の平均・標準偏差・最小/最大、相関行列。fixed集団の12選手の値(例示ではなく全件)。

## 失敗条件

1〜7のいずれかの不一致、既定の履歴・snapshotの変化、凍結成果物の変更、結果後の規則(特性・範囲・閾値)の変更。

## 限界

特性の範囲(0.15〜0.85)・一様分布は未校正の仮説。2特性の独立は構造の仮定で、現実の性格の相関を主張しない。T027では何にも使わないので、「個体差が行動に影響する」ことはまだ示していない(T028)。ID表ではなくseedから決まるため、同じseedなら同じ値(再現性)だが、同じ選手IDでもseedごとに別人になる。fixed集団の`ambition`等(手書き定数)とは独立に生成される。
