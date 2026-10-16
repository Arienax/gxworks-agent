# GXWorks Agent

[简体中文](README.zh-CN.md) · [English](README.md)

GXWorks Agent は GX Works2 向けのローカルエンジニアリングワークベンチです。
Web と MCP は同じ Python アプリケーションサービスを使用します。PLC プロジェクト
の作成・確認、ラダー/ST/FBD 候補の作成とレビュー、承認後の GX Works2 への
受け渡しに対応します。このソース公開には GXW 編集コードと空のプロジェクト
テンプレートが含まれます。

## Windows でソースから起動

Python 3.10 以降と Node.js 22.14 以降をインストールしてください。ネイティブ操作
には、別途適法に取得した GX Works2 が必要です。

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
.\build-web.bat --no-pause
.\start-web.cmd
```

単独 MCP の入口は `gxworks-agent-mcp.cmd` です。Web 用の Python 環境を共有
できます。MCP のみ使う場合は `.venv-mcp` に `requirements/mcp.txt` を
インストールしてください。フロントエンドのみのビルドは
`npm --prefix web ci` と `npm --prefix web run build` で実行できます。

`src/gxw/templates/` には FX3U の基準プロジェクト、ノード・トークン定義、
CPU 別の空の FBD プロジェクト 57 個が含まれます。アプリをコピーまたは
パッケージ化する際はこれらを保持してください。テンプレートの存在は各 CPU の
実機動作検証を意味しません。

三菱電機のマニュアル、マニュアルから生成した知識データベースと完全な命令定義は
再配布権を確認できていないため含めていません。アプリはそれらがなくても起動
できますが、マニュアル根拠付き RAG と一部の詳細な命令情報には、利用権のある
資料からローカルで索引を作成する必要があります。

設備に適用する前に、CPU、配線、安全設計を確認し、対象の GX Works2 で
コンパイル・チェックを行い、対象設備で動作を検証してください。

[今回の変更](CHANGELOG.md)、[貢献ガイド](CONTRIBUTING.md)、
[ライセンス範囲](LICENSE-SCOPE.md)、[第三者通知](THIRD_PARTY_NOTICES.md)を参照
してください。プロジェクト作成者が権利を持つソースには
[Apache License 2.0](LICENSE)を適用します。旧版のライセンスは維持されます。
