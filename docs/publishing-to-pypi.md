# 发布到 PyPI

`openloop-mas` 这个名字目前在 PyPI 上**尚未被占用**（2026-11-13 实测
`https://pypi.org/pypi/openloop-mas/json` 返回 404），仓库里也**没有任何
secret**（`gh secret list` 为空）。

所以默认走 **Trusted Publishing**：GitHub 在 job 里签一个 OIDC token，
PyPI 拿它换成一个短期的上传 token。**仓库里不需要存任何长期密钥。**

## 一次性登记（只有这一步需要人做）

1. 打开 <https://pypi.org/manage/project/openloop-mas/settings/publishing/>
   （第一次会先创建一个项目，需要先上传一次 sdist 才能建项目，见下面的
   「没有项目怎么办」）。
2. 填：
   - Provider: **GitHub Actions**
   - Organization: **tffgwy**
   - Repository: **openloop-mas**
   - Workflow name: **release.yml**
   - Environment name: 留空
3. 保存。

## 当前待补发：v0.7.15

`v0.7.11` ~ `v0.7.15` 的 GitHub Release 已发布（CI 四绿，wheel / sdist 都在），
但 **PyPI 上还没有 `openloop-mas`**——`release.yml` 的 Trusted Publishing 步骤
真实执行过，PyPI 返回：

```
invalid-publisher: valid token, but no corresponding publisher
(Publisher with matching publisher was not found)
```

即：仓库里**还没有登记过** Trusted Publisher（`gh secret list` 为空，
所以走的不是 token 路径）。登记完成后补发：

```bash
gh workflow run release.yml -f tag=v0.7.15
```

或者按上面的「一次性登记」把 Trusted Publisher 挂好，让**下一次**打 tag
自动发布。

## 之后每次发版

```bash
git tag -a v0.7.2 -m "v0.7.2"
git push origin v0.7.2
```

`release.yml` 会自动构建 → 校验 → 建 GitHub Release → 推到 PyPI。

## 两条发布路径（互斥）

| 条件 | 走的路径 | 需要什么 |
| --- | --- | --- |
| 仓库 secret `PYPI_API_TOKEN` 存在 | `twine upload` | 一个 PyPI API token |
| 没有 secret | `pypa/gh-action-pypi-publish` | pypi.org 上登记过一次 Trusted Publisher |

两条路径都配了的话，token 路径优先。PyPI 上传失败**不会**让 GitHub
Release 变成红色 —— Release 是主产物，PyPI 是附加产物。

## 没有项目怎么办

Trusted Publisher 必须挂在一个已存在的 PyPI 项目上。如果
`openloop-mas` 这个项目还没建，最简单的做法是：

```bash
# 建一个 PyPI API token（pypi.org -> Account settings -> API tokens）
# 然后在本地：
python -m pip install twine build
python -m build
python -m twine upload dist/*
```

上传第一次成功后项目就存在了，之后 Trusted Publishing 就能挂上去，
从此不需要 token。

## 验证发布成功

```bash
python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/openloop-mas/json'))['info']['version'])"
```

## 不要做的事

- 不要把 PyPI token 写进仓库、workflow 文件或任何提交；
- token 只通过 `gh secret set PYPI_API_TOKEN` 写进仓库 secret，
  **不要在对话或日志里回显明文**；
- 名称一旦占用就不能改，`openloop-mas` 不要再换别的名字试探。
