# Third-party dependencies

| Dependency                                            | Pin     | License                                     | Deployment                                                    |
| ----------------------------------------------------- | ------- | ------------------------------------------- | ------------------------------------------------------------- |
| [Three.js](https://github.com/mrdoob/three.js)        | 0.180.0 | MIT, copyright 2010–2025 three.js authors   | Bundled into `/app.js`, including the complete license banner |
| [esbuild](https://github.com/evanw/esbuild)           | 0.25.10 | MIT, copyright Evan Wallace                 | Build only                                                    |
| [Playwright](https://github.com/microsoft/playwright) | 1.58.0  | Apache-2.0, copyright Microsoft Corporation | Browser validation only                                       |

Prettier 3.6.2 (MIT) is used for source formatting during development only.

Dependency pins and integrity hashes are in `package-lock.json`. `npm ci` installs
the original LICENSE files with these packages. Chromium downloaded by Playwright
is used only for development tests and is never packaged with Python.

## Deployed Three.js license

The MIT License

Copyright © 2010–2025 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
