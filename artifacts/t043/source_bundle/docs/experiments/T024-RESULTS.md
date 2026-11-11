# T024 結果 — 年度ごとの順位表(派生集計)

実装・実験: 2026-11-10。契約: [T024](../tasks/T024.md)、事前条件: [PROTOCOL](T024-PROTOCOL.md)、判断: [D039](../DECISIONS.md)。規則と条件は実装前にコミットし、結果後に変えていない。Python 3.13.3(uv)、macOS arm64。

## 結論

- `soccer_sim/standings.py`の`league_standings(world)`を追加した。`league_match_completed`イベントだけから、年度(T023の`season_progress`で割当)ごと・クラブごとの試合数・勝分敗・得失点・勝点・順位を導く純関数。worldの状態、乱数、イベント、snapshot、save版は変えず、保存もしない。成功条件1〜8を満たした。
- 新ファイルは指紋対象12ファイルの外にあり、既存の`soccer_sim/*.py`は1バイトも変えていない。runtime source hashはT023と同じ`b6eb7dac…06e6f9`。

## 検査

| 条件 | 結果 |
|---|---|
| 1 既存不変 | `soccer_sim/`のT023との差は`standings.py`の追加のみ。identity(source hash含む)がT023 source_bundleと一致。既定14条件の126日digestが実装前基準と一致、35日がT018と一致 |
| 2 派生のみ | 42セルすべてで呼出し前後のsnapshot digestが同一。クラブ口座を参照しないことは、別world(条件違い)のイベントに差し替えると結果がそのイベントに従い、元の結果と異なることで確認(他条件のイベントを使った入替え1組) |
| 3 手計算 | 実装前に手で作った2〜4年度の合成ケース(13試合+非試合イベント2件、年度境界の日21・63、勝点同点→得失点差、得失点差同点→総得点、総得点同点→勝数、全同点→club_id、試合なし・未完了の年度)が一致。実装後の最初の実行で一致し、期待値は修正していない |
| 4 会計との整合 | 42セルで、全年度の合算がclub_accountsのpoints/matches_played/wins/draws/losses/goals_for/goals_againstと一致。試合は18試合日×2=36、年度ごとの試合数は日から数えた期待値と一致 |
| 5 独立再実装 | 別の書き方(生snapshot dictだけ、天井除算の年度、クラブごとの結果リスト、comparator整列)が42セルすべてで本実装と表全体(行・順序・フラグ)で一致。合成ケースも一致 |
| 6 再計算 | 42セルすべてを保存し、全再演reader・信頼readerの両方で読んだworldの`league_standings`が元と完全一致 |
| 7 不正値 | 年度長0、未知クラブ、同一クラブ、負/bool/floatの得点、world.dayより後の試合が7件ともValueError |
| 8 検査 | 全unittest 242件合格(下記)、旧成果物3,785ファイルのhash不変、source_bundle保存 |

## 観察(記述統計、探索的)

- 年度の長さで順位表の意味が変わる。L=21は1年度が3試合日(各クラブ3試合)しかなく、club_idだけで決着した行(`tied_with_previous`)が84年度行のうち16、10セルに出た。L=42は42年度のうち3、L=84は28年度で0。総計19行(13セル)。短い年度ほど便宜的な決着に頼るので、年度を意味のある単位にするなら年度長は42以上が妥当に見える(これは観察で、年度長の決定ではない)。
- 例: open_solidarity(L=21)の6年度の1位はclub_b, c, c, a, c, a。順位表が年度ごとに入れ替わることはsim上で確認できた。ゲームとして面白いかは測っていない。

## 限界

- 派生物で、保存・公開(Policyや選手への開示)をしない。昇降格・優勝判定・直接対決での決着は未実装。club_id昇順の決着は便宜。
- `standings.py`は指紋scope外。進行やsnapshotがこれを使うようにするなら、scopeを変える別task(新たなsave版)が要る。
- 4クラブ・総当たり1周3試合日の小さな世界。順位の接戦頻度や意外性は測っていない。
- 最初の実験実行(`run-log-attempt1-failed.txt`)は、L=84で126日目に2年度目が未完了(`126//84=1`)なのに私の検査が全年度完了を期待した検査側の誤りで停止した。`standings.py`の出力とは無関係に、検査の期待を「ceil(126/L)年度、完了=s×L<=126」へ直して再実行した(プロトコルの`complete`の定義と一致)。ルールは変えていない。

成果物: `artifacts/t024/{preflight,run,review}/`、`artifacts/t024/run-log.txt`と失敗した初回の`run-log-attempt1-failed.txt`、凍結ソース`artifacts/t024/source_bundle/`。実行: `uv run --python 3.13.3 python -B experiments/t024.py --out artifacts/t024/run`(新規ディレクトリのみ)。
