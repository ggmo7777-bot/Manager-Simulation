# 自律的に発展するサッカー界シミュレーション

独立した選手・監督の経験と選択から、再現可能で理由を追跡できる世界の歴史を生むプロジェクト。最強の競技AIを主目的にしない。

**現在: T010（M4後半 / VS1閉ループ完了）まで実装・検証済み。**
- T001〜T007（作成者: `Sol 6.1 High` および当時の主担当）では、選手2人・監督2人の環境で監督ごとの私的な観測/知識と選手から相手別の信頼を分離し、複数の提案から独立に練習/休息を選び実施経験から相手を評価する育成核を構築した（旧1監督モードも比較基準として保持）。
- **T008・T009・T010（作成者: Antigravity / Gemini 4 Argon — T001〜T007の既存作成者とは別の作成者）** では、まずT008で4選手・1ボール・2ゴール・固定tickの最小連続2D競技核（`world_kind='match_2d'`, `save=8`）を分離実装し、T009で1クラブ内の最小出場機会閉ループ（`world_kind='selection_loop'`, `save=9`）を検証したうえで、今回の**T010**で2クラブ（`club_a`: 監督 `c1`、`club_b`: 監督 `c2`）・6選手（`p1..p6`、所属人数不変条件 `2 <= roster <= 4`）×84日（週1回の直接2D対決×12試合＋移籍窓口 Day 28, Day 56）の**2クラブ直接2D対戦と3者相互合意移籍を接続した最小キャリア閉ループ**（`world_kind='transfer_loop'`, `save=10`）を実装・検証した。固定イベント移籍を一切作らず、選手本人・放出側監督・獲得側監督の3者の私的認識が合致したときのみ成立する移籍によって、初期所属クラブで過小評価されていた高潜在選手 `p3` が新天地で出場機会と成長を得て2クラブ間の後半戦得失点差を劇的に転換させること、および3者の主観の単一要因対照（`fair_c1_prior_p3`, `low_c2_scouting_p3`, `high_loyalty_p3`）で移籍と勢力図変化の因果が分離されることを確認した。4〜8クラブの金銭台帳・リーグ戦（M5）や長期世代交代（M6）はまだ実装していない。

まず [現在の状態と検証結果](docs/STATUS.md)、次に [システム設計](docs/DESIGN.md) と [段階的ロードマップ](docs/ROADMAP.md) を読むと全体を把握できる。

## 依頼された10項目

| 成果物 | 資料 |
|---|---|
| 1. プロジェクト憲章 | [目的・優先順位・必須原則](docs/CHARTER.md) |
| 2. 未確定事項 | [候補・必要な検証・変更の影響](docs/OPEN-QUESTIONS.md) |
| 3. システム全体像 | [構成図・状態/観測/時間/試合/経済/ログ](docs/DESIGN.md) |
| 4. 個体の状態・行動・学習 | [選手/監督、A/B/C、Policy方式の比較](docs/DESIGN.md) |
| 5. 最小実験環境 | [T001と次のVS1の範囲](docs/ROADMAP.md) |
| 6. 段階的ロードマップ | [開発順序と理由](docs/ROADMAP.md) |
| 7. 成功・失敗・比較実験 | [段階別ゲート](docs/ROADMAP.md)、[初回事前プロトコル](docs/experiments/T001-PROTOCOL.md) |
| 8. 最初の実装タスク | [T001作業契約](docs/tasks/T001.md)、[Sol向け引き継ぎ](docs/SOL-HANDOFF.md) |
| 9. 設計変更と実験の管理 | [管理方法](docs/GOVERNANCE.md)、[判断ログ](docs/DECISIONS.md) |
| 10. 研究課題・延期項目 | [未解決課題](docs/OPEN-QUESTIONS.md)、[調査と一次資料](docs/RESEARCH.md) |

設計は最終仕様ではない。確定した原則と試用する実装方法を分け、実験結果によって更新する。

## 実行

Python 3.13以上。確認環境はmacOS arm64/CPython 3.13.3。標準ライブラリのみで、依存パッケージのインストール、GPU、有料APIは不要。

ターミナルでこのフォルダに移動して実行する。

```sh
cd /Users/yamaguchitoshi/Documents/soccer
python3 -m unittest discover -s tests -v
python3 -m soccer_sim run --condition transfer_coupled_transfer --seed 17 --days 84 --out artifacts/local/transfer84
python3 -m soccer_sim run --condition select_coupled_feedback --seed 17 --days 84 --out artifacts/local/select84
python3 -m soccer_sim run --condition multi_learning_adaptive --seed 17 --days 84 --out artifacts/local/run84
python3 -m soccer_sim run --condition match_baseline --seed 17 --days 40 --out artifacts/local/match40
```

出力フォルダに、真の状態と全履歴を含む`snapshot.json`、分析用`events.jsonl`、集計`summary.json`、条件/版の`manifest.json`ができる。これらの真値ログは研究者向けで、個体の判断入力には使わない。同じ出力先を指定するとそのフォルダの成果物を更新するため、条件ごとに別の名前を使う。

保存から追加42日を実行する。

```sh
python3 -m soccer_sim run --condition transfer_coupled_transfer --seed 17 --days 42 --out artifacts/local/first42
python3 -m soccer_sim resume --snapshot artifacts/local/first42/snapshot.json --days 42 --out artifacts/local/resumed84
```

`--days`は追加日数。現コードではソース/ルール/Policy/乱数/保存版が違うsnapshotを拒否する。コード変更後は旧runをそのまま読み込めるとは限らない。複数監督のsave7は初期条件から全履歴を再演して照合するため、読込費用は経過日数に比例する（T008のsave8、T009のsave9、およびT010のsave10は再演なしの1パス線形整合監査readerを採用）。

T010の2クラブ直接2D対戦・相互合意移籍閉ループ（4条件×60seed＋単一要因対照3条件×30seed）、T009の1クラブ育成・選考・2D競技閉ループ、T008の2D競技核、またはT007の監督予測学習×相手別信頼更新の4条件×60seedを再実行する。T001〜T006も各 `--suite` で選べる。`run`で条件を省略したbaselineは、従来の1監督・非負丸め・固定信頼・監督予測学習OFF・旧方法選択を維持する。2クラブ移籍閉ループは `--condition transfer_coupled_transfer`（または `transfer_coupled_stay`, `transfer_decoupled_transfer`, `transfer_decoupled_stay`, `transfer_fair_c1_prior_p3`, `transfer_low_c2_scouting_p3`, `transfer_high_loyalty_p3`）、1クラブ選考閉ループは `--condition select_coupled_feedback` 等、2D競技核は `--condition match_baseline` 等で選べる。

```sh
python3 -m soccer_sim experiment --suite t010 --out artifacts/local/comparison-t010
python3 -m soccer_sim experiment --suite t009 --out artifacts/local/comparison-t009
python3 -m soccer_sim experiment --suite t008 --out artifacts/local/comparison-t008
python3 -m soccer_sim experiment --suite t007 --out artifacts/local/comparison-t007
```

## 現在の実測

- **自動テスト109件が合格**（T001〜T007の83件＋T008の10件＋T009の8件＋T010の8件）。
- **T010（2クラブ直接2D対戦と3者相互合意移籍の最小キャリア閉ループ、作成者: Antigravity / Gemini 4 Argon）**: 主比較4条件×60seed×84日＝240run、3者合意単一要因対照3条件×確認30seed＝90run、84日連続と42+42日保存再開、および1,000日（142試合・36,200イベント）＋14日再開のsnapshot/JSONL完全一致を確認（1,000日1パス読込 `0.376s`）。確認30seedで①相互合意移籍による個人キャリアとクラブ間成績の転換（`coupled_transfer - coupled_stay`: 初期過小評価の `p3` が30シード中28シードで移籍 `+0.9333`、出場 `3.43 -> 7.47` 試合、`p3` 満足度 `+0.739`、`p3` 成長 `+0.0087`、`club_b - club_a` 通期得失点差 `-1.57 -> +10.80`、後半6試合得失点差 `-1.53 -> +8.63`）、②育成結合×移籍窓口の正の交互作用（通期得失点差 `+12.00`、後半得失点差 `+9.73`、`club_b` シュート `+12.90`）、③3者の主観単一要因対照（放出側 `c1` の初期評価適正化 `fair_c1_prior_p3` で移籍率 `-0.7333`・得失点差 `-14.87`、獲得側 `c2` のスカウト評価低下 `low_c2_scouting_p3` で移籍率 `-0.4667`・成長 `-0.0067`、選手愛着上昇 `high_loyalty_p3` で移籍率 `-0.7333`・成長 `-0.0071`）を確認。Policy関数を呼ばない独立監査で3,780決定＋748フィードバック＝4,528件を再計算して100%一致。
- **T009（育成・監督選考・2D競技の最小閉ループ、作成者: Antigravity / Gemini 4 Argon）**: 主比較4条件×60seed×84日＝240run、追加対照3条件×確認30seed＝90run、84日連続と42+42日保存再開、および1,000日（142試合・22,270イベント）＋14日再開のsnapshot/JSONL完全一致を確認（1,000日1パス読込 `0.190s`）。確認30seedで①育成$\to$2D競技（`coupled_feedback - decoupled_feedback`: 得失点差 `+24.53`、後半6試合得失点差 `+18.83`、保持 `+81.73` tick、シュート `+28.43`、パス成功 `+10.07`）、②2D競技$\to$監督選考・選手行動（`coupled_feedback - coupled_prior`: 挑戦者 `p3` 出場 `0.00 -> 5.40` 試合、`p3` 成長 `+0.0148`、平均成長 `+0.0031`、先発選考不一致率 `45.00%`・監督行動不一致率 `57.96%`・選手行動不一致率 `46.62%`）、③初期評価バイアス是正（`fair_prior_p3`: 真の能力変更なしで `p3` 出場 `5.40 -> 7.57` 試合、`p3` 成長 `+0.0050`、平均成長 `+0.0040`、得失点差 `+1.90`）と機械的ローテーション（`rotation_feedback`）の質的違いを確認。Policy関数を呼ばない独立監査で1,860決定＋372フィードバックを再計算して100%一致。
- **T008（最小2D競技核、作成者: Antigravity / Gemini 4 Argon）**: 主比較4条件×60seed×40tick＝240run、全4条件で40tick連続と20+20tick保存再開のsnapshot/JSONLがバイト一致。確認30seedで能力介入（`home_skill_plus`: 得失点差 `+1.97`、所持 `+4.80` tick、シュート `+2.53`）、位置介入（`home_position_plus`: 敵陣滞在 `+13.60` tick、所持 `+3.23` tick、`home` 得点 `+0.37`）、個人スタイル介入（`home_pass_style`: パス試行 `+10.53`、パス成功 `+8.67`、行動不一致 `39.71%`）がそれぞれ独立な経路で競技履歴を変え、単純対照（`always_shoot`: 得失点差 `-2.80`、`ball_camp_wait`: `-2.90`）が支配しないことを確認。Policy関数を呼ばない独立監査で800判断を再計算して100%一致。
- **T007（2監督の私的認識と相手別信頼、作成者: Sol 6.1 High / 旧主担当）**: 4条件すべてで84日連続と42+42日再開がバイト一致。確認30seedの予測学習あり条件で相手別信頼を更新すると選手の行動16.94%が異なった。同方法の助言は得点へ加算しないが事前に受け取った予測は実施経験と個別に照合する。
- **過去資産と互換性**: T001〜T009の凍結485ファイル不変、T009全240本の意味投影一致を確認。またT001〜T007の既実装6要素の改善感度（開ループ直接効果の分離、監督残差学習率 $\alpha_{\text{res}}=0.10$ による予測MSE改善、両予測負時の提案抑制ガード、非負助言項による `p2` 経験欠損の緩和、初期観察位相差による重複観察の90.8%削減、1パス線形監査readerによる1000日読込3.15倍高速化）をインメモリ対照実験で検証し記録した。

[T010の実験結果](docs/experiments/T010-RESULTS.md) · [T010レビュー](docs/experiments/T010-REVIEW.md) · [2クラブ移籍閉ループの実装](soccer_sim/transfer_loop.py) · [T010自動テスト](tests/test_t010.py)

[T009の実験結果](docs/experiments/T009-RESULTS.md) · [T009レビュー](docs/experiments/T009-REVIEW.md) · [出場機会閉ループの実装](soccer_sim/selection_loop.py) · [T009自動テスト](tests/test_t009.py)

[T008の実験結果](docs/experiments/T008-RESULTS.md) · [T008レビュー](docs/experiments/T008-REVIEW.md) · [2D競技核の実装](soccer_sim/match_2d.py) · [T008自動テスト](tests/test_t008.py)

[T007の実験結果](docs/experiments/T007-RESULTS.md) · [T007レビュー](docs/experiments/T007-REVIEW.md) · [複数監督の実装](soccer_sim/multi_coach.py) · [T007自動テスト](tests/test_t007.py)

[T001の結果](docs/experiments/T001-RESULTS.md)、[T002の結果](docs/experiments/T002-RESULTS.md)、[T003の結果](docs/experiments/T003-RESULTS.md)、[T004の結果](docs/experiments/T004-RESULTS.md)、[T005の結果](docs/experiments/T005-RESULTS.md)、[T006の結果](docs/experiments/T006-RESULTS.md)も保持する。`artifacts/t001/`〜`artifacts/t010/`は証拠として保管し、再実行には`artifacts/local/`など別の出力先を使う。各`source_bundle/`はその版の凍結コピーで、日常の開発先はルートの`soccer_sim/`。旧snapshotは対応する旧コードで読む。

次の候補は **M5（小規模世界：4〜8クラブのリーグ日程・順位表、有限台帳・契約・育成報酬を接続する最小リーグ経済環境：T011候補）** の最小タスク定義。道筋は[ロードマップ](docs/ROADMAP.md)に記録した。候補は未着手で、実験結果に応じて順序を見直す。
