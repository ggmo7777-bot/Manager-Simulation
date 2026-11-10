# T017 結果 — sampled集団のCLI接続

2026-11-09。実装Sol 6.1 High、設計・独立検証・記録Codex。[事前契約](../tasks/T017.md)。

通常の`run`で7つのリーグ条件すべての`_sampled_population`接尾辞を受け付ける。CLI内の同じリーグ条件辞書をchoicesとdispatchで使い、既存`config_for_condition`へ設定構築を委ねた。既定baseline、固定条件、他世界、T011実験の条件集合は維持する。

```sh
python3 -B -m soccer_sim run --condition league_open_solidarity_sampled_population --seed 100 --days 14 --out artifacts/local/t017-sampled14
python3 -B -m soccer_sim resume --snapshot artifacts/local/t017-sampled14/snapshot.json --days 21 --out artifacts/local/t017-sampled35
python3 -B -m soccer_sim branch --snapshot artifacts/local/t017-sampled14/snapshot.json --kind player_trust --player p3 --coach c1 --value 0.05 --reason "信頼差の比較" --days 21 --out artifacts/local/t017-trust-low35
```

出力先は新しいディレクトリを使う。接尾辞なしは従来のfixed条件。resume/branchは保存されたpopulation_modeを維持し、読み込み時に母集団を変更しない。

## 検証結果

| 検証 | 実測 |
|---|---|
| 全unittest | 196件合格、103.318秒 |
| 新規CLI統合テスト | 6件合格、7.519秒。全7条件の初期化/4成果物、baseline、入力拒否、再開/分岐、旧保存拒否を含む |
| 高速ゲート | 50件合格、2.798秒。新規CLIからhelpと不正入力の2件を追加し、重い再開テストは全件側で実行 |
| 35日比較 | fixed/sampled各7条件、seed100の計14世界。CLIとAPIでsnapshot/events/summaryが一致 |
| 旧版比較 | 14世界で凍結T016 APIと全状態/イベント/評価が一致。identityとsnapshot由来hashのみ除外 |
| 保存再開 | sampledの14日保存をCLIで35日へ再開し、直接35日とsnapshot/events/summaryが一致 |
| 分岐 | sampledの信頼介入→進行→再開がAPIと一致。親の4成果物は不変 |
| 入力境界 | helpに全7sampled条件。未知名・非リーグsampled・重複接尾辞はexit2で拒否し、出力先を作らない |
| 互換性 | T016保存はsource hash不一致として拒否し、旧ファイルも出力先も変更しない |
| 独立監査 | 2,993取引で口座・累計・保存則一致。167要求、10配当決済を照合し、実重複請求3件を拒否 |
| 旧成果物 | 1,911ファイルのhash不変 |

比較・CLI再開・監査の全体は24.127秒。全テストと並行した実測で、単独性能ベンチマークではない。今回の比較は起動経路の接続確認であり、新しい多seed頑健性実験ではない。

## 版と保管

packageの変更は`__main__.py`だけ。他の全packageファイルはT016凍結版とバイト一致。save16、T015進行ルール、T013 Policy、T016評価版、初期生成・学習・試合・会計は維持した。

source hash: `b542bfdcea7b87eea39e01aa733105ca56ac225bc63adbd468d64b3c59b9b3d8`。現在はpackage全体をhashするので、CLI変更でも旧保存を現行readerで読めない。旧保存はT016の凍結ソースで読み、指紋を手で書き換えない。runtime/CLI/分析のhash分離は別候補。

証拠: [比較・監査](../../artifacts/t017/comparison/summary.json)、[実際のCLIコマンド](../../artifacts/t017/comparison/commands.json)、[全テスト](../../artifacts/t017/review/tests-all.txt)、[manifest](../../artifacts/t017/manifest.json)、[ソースmanifest](../../artifacts/t017/source_bundle/manifest.json)。

再現: `python3 -B experiments/t017.py --out <新規出力先>`。凍結版を使うときは`--data-root`で旧成果物を含む作業ディレクトリを指定する。現在のrun CLI自体は既存出力先を更新する仕様のため、凍結先へ出力しない。

生成分布の校正・要因分解、世代交代、毎季市場、C、全再演readerの高速化は未実施。T016の180世界の制度効果を今回再分析したとは扱わない。
