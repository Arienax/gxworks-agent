# DeepSeek Harness Python SDK

The `deepseek_harness` package is vendored from [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness), revision `687f282e8625c6b8d11ff58211a90524be2e4317`, at `python/sdk/src/deepseek_harness`. See [LICENSE](LICENSE).

GXWorks Agent supplies its bundled independent DSH SDK executable explicitly.
Local SDK changes start its Windows subprocess without opening a console
window and report shutdown failures when the caller requests a durable close.
