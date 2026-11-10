# 作業の継続

- 最初にREADME.md、docs/CHARTER.md、docs/STATUS.md、対象タスクと過去実験を読む。
- 最上位目的は独立個体の経験と選択から再現可能な歴史が生まれること。勝率やドラマの発生数だけを最適化しない。
- 設計は仮説。実装済み範囲と将来案を区別し、重要変更はdocs/DECISIONS.mdに理由・副作用・比較・戻し方を残す。
- PolicyへWorldの真値を渡さない。Aの状態更新、Bの認識更新、CのPolicy学習を分ける。
- 次の実装は依頼された最小taskだけ行う。docs/tasksの候補は全ての着手指示ではない。
- Python標準ライブラリのみの現状。検証は `python3 -m unittest discover -s tests -v`。
- 旧snapshotはpackageのsource hashが異なると拒否される。変更前に該当taskのartifacts/<task>/source_bundleとmanifestを確認し、旧結果を上書きしない。T001〜T007の凍結成果物を保持する。
- T007の複数監督readerは初期条件から全履歴を再演する暫定方式。読込費用は日数に比例し、記録外の途中介入は復元しない。
- 参照順: docs/GOVERNANCE.md、docs/DECISIONS.md、docs/RESEARCH.md、docs/SOL-HANDOFF.md。
