# T027 結果 — 性格の潜在特性(選手の真の状態のみ)

実装・実験: 2026-11-10。契約: [T027](../tasks/T027.md)、事前条件: [PROTOCOL](T027-PROTOCOL.md)、判断: [D042](../DECISIONS.md)。特性・範囲・閾値・条件は実装前にコミット(e877110)し、結果後に変えていない。Python 3.13.3(uv)、macOS arm64。**特性の範囲と分布は未校正の仮説で、何にも使われていない。**

## 結論

- `LeagueEconomyConfig.personality`(`"none"`既定、`"latent_v1"`)と`LeagueWorldState.player_personality`を追加した。有効時のみ、全12選手に`discipline`(規律性)と`emotional_stability`(感情安定性)を、別チャネル`personality:<特性名>`のキー付き決定的draw(0.15〜0.85一様、`round6`)で生成して保持する。判断・成長・観測・イベントのどこでも読まない。成功条件1〜7をすべて満たした。
- 野心・助言への従順/自律・忠誠は`PlayerMind`に既にある(`ambition`・`autonomy`・`loyalty_bias`)ので複製していない。追加した2特性は、全選手で同一の直書き定数(満足度の反応幅+0.14/-0.11/+0.18、休養選好0.012)に対応する個体差の候補で、T028が最初に使う。**まだ個体差が行動に効くことは示していない。**

## 検査

| 条件 | 結果 |
|---|---|
| 1 既定不変 | 既定14条件の126日digestがT023基準と一致、35日がT018と一致。既定のconfigに`personality`、worldに`player_personality`なし。`personality="none"`明示の14世界は既定とsnapshotが同一 |
| 2 履歴不変 | 14世界×seed{100,101,102}=42組で、有効にしても`personality`と`player_personality`を除くsnapshot全体(events・minds・coaches・台帳含む)・summary・イベント数が無効と完全一致。`season_length_days=42`・`aging_start_age=29`・`aging_curve="hypothesis_v1"`併用の14世界でも一致 |
| 3 漏れ検査 | (a)動的: 14世界で特性を全員0.15/全員0.85に差し替えても126日の履歴が生成値の世界・無効の世界と完全一致。(b)静的: `league_economy.py`以外の28 moduleに3語の出現なし。`league_economy.py`で参照する関数は許可リスト8個(`__post_init__`・`to_dict`・`has_save_fields`・`_build_initial_world`・`_validate_league_invariants`・`_snapshot_payload_unchecked`・`load_league_snapshot_trusted`・`_personality_traits`)に限られ、`decide_*`・進行/成長/移籍関数に参照なし。決定関数が特性を読む合成コードを同じ検査が検出する(感度確認) |
| 4 生成器の忠実性 | 独立実装(sha256→上位53bit)が14世界相当(3seed×12選手×2特性)で完全一致。seed0..999の24,000値: 範囲は[0.150136,0.85]、平均0.4992/0.5025(基準0.50±0.01)、標準偏差0.2019/0.2019(基準0.2021±0.006)、KS統計量0.0065/0.0100(臨界0.0149)。相関は2特性間-0.007、既定のambition/loyalty/autonomyとは全て|r|≤0.013(基準0.03)。academy−buyer平均差は-0.001/-0.003(基準0.02)、選手IDごとの平均は0.491〜0.514(基準±0.03)。同じ選手IDでseed100と101の値は全員異なる。sampled人口の既存draw(profile・impression)は有効/無効で、7条件×seed0..99の700組の構築結果が一致 |
| 5 再演と保存 | 有効14世界を保存し、全再演・信頼の両readerが`_assert_same`で一致。既定14世界のsnapshot(キー無し)も両readerで一致。分岐world(30日→fork→+60日)は親と同じ特性で全再演readerと一致。ステップ実行と一括実行が一致。`has_save_fields`は有り/無しを受理し未知キーを拒否 |
| 6 改ざん検出 | 7種(値変更・選手削除・特性キー削除・余分な特性キー・範囲外値・無効なのに追加・有効なのに削除)×3読み方(全再演、信頼、manifestの`snapshot_sha256`を改ざん後に合わせて再計算した信頼)=21件すべて拒否。T022のイベント削除などの限界と違い、本taskの値はseedから再計算できる不変条件で、manifest偽造でも止まる |
| 7 不正値・検査 | `personality`の未知文字列・int・空文字の3件がValueError。全255テスト合格(`artifacts/t027/tests-all.txt`)、旧成果物4,382ファイルのhash不変(`preflight/inventory-verify.json`)、source_bundle保存 |

## 記述(合否外)

特性の標本(seed0..999×12選手): discipline 平均0.4992・標準偏差0.2019、emotional_stability 平均0.5025・標準偏差0.2019。seed100のp1は(discipline 0.3147、emotional_stability 0.4312)、p3は(0.2285、0.7498)、p10は(0.7243、0.6456)で、全12選手の値は`artifacts/t027/run/summary.json`の`generator.fixed_seed100`にある。fixed集団の手書き`ambition`等とは独立に生成される。

## 限界

- 範囲(0.15〜0.85)と一様分布は未校正で、2特性の独立は構造の仮定。現実の性格の相関や、特性が経験で変わることは扱っていない(T029以降)。
- 何にも使わないため、「性格の違いが行動と履歴を変える」ことはT028まで未検証。本taskは、使う前に隠せること・履歴を変えないこと・再現できること、の土台の検証にとどまる。
- 特性名と範囲を結果前に固定した。分布検査の閾値は設計上の許容幅で、検出力の議論はしていない(実測値は閾値の1/2以下に収まった)。
- 実装中の失敗: 検査コード(`check_generator`)の初回テストでacademyクラブIDのtuple/listの連結ミスで止まった。検査側の誤りで、規則・閾値は不変。

成果物: `artifacts/t027/{preflight,run}/`、`artifacts/t027/run-log.txt`、`artifacts/t027/tests-all.txt`、凍結ソース`artifacts/t027/source_bundle/`。実行: `uv run --python 3.13.3 python -B experiments/t027.py --out artifacts/t027/run`(新規ディレクトリのみ、約3.7分)。
