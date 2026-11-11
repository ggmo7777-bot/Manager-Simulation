# T023 結果 — 年度区切り(season境界)

実装・実験: 2026-11-10。契約: [T023](../tasks/T023.md)、事前条件: [PROTOCOL](T023-PROTOCOL.md)、判断: [D038](../DECISIONS.md)。条件は実装前に固定し、結果後に変えていない。Python 3.13.3(uv)、macOS arm64。

## 結論

- M6の最初の最小単位として、年度の区切りを追加した。`LeagueEconomyConfig.season_length_days`(整数、0=無効が既定)、日から年度を導く`season_progress(world)`(状態を持たない派生値)、年度末ごとにhookを呼ぶ`advance_league_seasons(world, seasons, on_season_end)`。**進行ルール・乱数・イベント・snapshotの中身は変えていない**。成功条件1〜6を満たした。
- 加齢・引退・新人・年度ごとの移籍窓・順位表・年度末イベントはまだ何もない。hookの中身は空で、次のtaskが記録付きイベントとして載せる。

## 選んだ理由

加齢(選手の年齢フィールド)は全選手のsnapshotを変え、既定worldのバイト同一性を壊し、「1年」の単位も未定義だった。年度の区切りは時間単位だけを足せるので、既定を保ったまま、加齢・引退・新人・毎季市場・順位表のすべてが載る場所を用意できる。年度末イベントを今回記録しないのは、イベントIDが連番で、足すと以降の全IDとcause_refsがずれ、「有効にしても履歴が同一」を検証できなくなるため。

## 検査

| 条件 | 結果 |
|---|---|
| 1 既定不変 | 既定14条件(35日)がT018 rerun-py313とsnapshot/events/summaryで一致(identity/hash除く)。既定14条件の126日snapshot digest(identity除く)が実装前に記録した基準と一致。configに`season_length_days`キーなし |
| 2 有効でも履歴同一 | 14世界×L∈{21,42,84}=42セル(126日、seed100)で、`season_length_days`を除いたsnapshot全体(events全件含む、例: open_solidarity@42は6,027件)が既定worldと完全一致。configの差は`season_length_days`の1キーのみ |
| 3 年度とhook | sampled open_solidarity L=42: 0日から3年度はhookが日42,84,126・完了数1,2,3、結果は`advance_league_days(126)`と`_assert_same`一致。10日から2年度は42,84で停止。年度末(42日)から1年度は84のみ。0年度は何もしない。日0..130の全日で`season_progress`が事前登録の式と一致 |
| 4 保存 | L=42の126日worldを保存し、全再演reader・信頼readerの両方で元と`_assert_same`、`season_progress`も一致({season 3, day_in_season 42, seasons_completed 3}) |
| 5 不正値 | 長さが負・bool・float・str・Noneは構築時にValueError。長さ0のworldでの`season_progress`・`advance_league_seasons`、年度数が負・bool・float、呼べないhookはいずれもValueErrorで、worldは変更されない(11件すべて拒否) |
| 6 検査 | 全238テスト合格(3.13.3。実験と並走のため818秒)、旧成果物3,597ファイルのhash不変、source_bundle保存 |

実験の実時間(595秒)は全テストと並走した値で、費用の指標ではない。

## 仮説の判定

- 機構(年度の導出・hook位置・既定不変・履歴同一): 支持。反証され得た点(configフィールド追加が保存の検証を壊す)は起きなかった。
- ゲームとして有用かは未判定: 年度が見えるだけで、順位表や世代交代はまだない。

## 限界

- 年度は時間単位であって、現実の年間日程とは無関係。年度長21/42/84は任意。
- 移籍窓(日28/56)は最初の年度にしか来ない既存制約が残る(毎季の市場は別task)。
- hookはworldを変更してよいが、記録外の変更は保存・再演で拒否される。変更の仕組み(記録付きイベント)は与えていない。
- `season_length_days`はconfigに含まれるため、保存のconfigを書き換えても再演は通る(履歴が同一のため。時間単位を書き換えるだけで、規則違反ではない)。

成果物: `artifacts/t023/{baseline,run,review,preflight}/`、`artifacts/t023/run-log.txt`、凍結ソース`artifacts/t023/source_bundle/`。実行: `uv run --python 3.13.3 python -B experiments/t023.py --out artifacts/t023/run`(新規ディレクトリのみ)。
