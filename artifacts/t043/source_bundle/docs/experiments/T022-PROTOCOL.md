# T022 事前実験条件

2026-11-10、実装前登録。契約: [T022](../tasks/T022.md)。結果を見て目標・条件・seedを変えない。

## 基準(実装前の実測、`artifacts/t022/baseline/baseline.json`)

Python 3.13.3、macOS arm64、seed100、open_solidarity(fixed/sampled)、既定`load_league_snapshot`の3回測定(秒):

| 世界 | 35日 | 84日 | 1000日 |
|---|---|---|---|
| fixed | 1.20 / 1.38 / 1.22 | 2.54 / 2.98 / 2.29 | 14.58 / 14.34 / 16.91 |
| sampled | 0.47 / 0.54 / 0.48 | 1.17 / 1.16 / 1.14 | 6.06 / 7.26 / 10.97 |

測定は他処理と並走した可能性があり、ばらつきが大きい(1000日sampled 6〜11秒)。そのため最終比較は同一プロセス内で全再演読込と信頼読込を交互に3回ずつ測り、中央値で比べる。

## 固定する成功条件

1. 同一性: `T011_ALL_CONDITIONS`7条件×fixed/sampled=14世界(seed100、35日)で、信頼読込の世界が全再演読込と`_assert_same`で完全一致し、`snapshot_payload`のdigestが一致し、再保存したsnapshot/events/summaryがバイト一致。
2. 継続: sampled open_solidarityの14日から信頼読込→21日進行が、全再演読込→21日進行と完全一致(resume)。14日で2件の記録介入(別種)を行った分岐を保存し、信頼読込で`interventions`が保持され、さらに1件の介入(多段)を追加でき、全再演読込と一致。
3. 負例(manifest整合のまま): 値の改変、型変更(float→int)、余計なキー、重複JSONキー、manifest欠落、manifest hash不一致、identity(source hash)不一致/旧save16 identityを全て拒否し、出力ディレクトリ・親ファイルを変更しない。
4. 速度(事前宣言の目標): 1000日(fixed/sampled)の信頼読込の中央値が全再演の中央値の1/3以下(3倍以上)、84日は1/2以下。35日は報告のみ。
5. 検査: 全unittest合格(Python 3.13.3)、既定14条件のsnapshotがT018 rerun-py313と(identity/hash除き)一致、既定readerのテスト不変、旧成果物inventoryのhash不変。

## 失敗条件

1〜3のいずれかの不一致/受理、速度目標の未達、既定readerの挙動変更、凍結成果物の変更、結果後の目標・条件変更。速度未達の場合は未達として記録し、PRの結論に反映する。

## 特性評価(合否に使わない)

manifestも偽造・再計算した改変(例: 現金の改変、1イベント削除、日数の改変)を全再演readerと信頼readerへ与え、どちらが拒否するかを表にする。信頼readerが受理するものは限界として明記する。

## 限界

単一マシン・単一seed100の速度。ばらつき大。信頼readerは署名ではなく完全性検査。保存・fork・resume保存の高速化は対象外。
