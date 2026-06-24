# 第二类网站 HTTP 可行性实际验证报告

## 验证方法
- 使用 httpx 发送 HTTP 请求模拟浏览器访问
- 测试型号: `RC0402FR-0710KL`（常见贴片电阻）
- 使用 Chrome 浏览器 DevTools 抓包验证 API 调用
- 从海外服务器发起请求（部分站点可能有地域限制）

---

## 验证结果总表

| # | 网站 | HTTP状态 | 能否纯HTTP获取数据 | 实际策略 | 备注 |
|---|------|----------|-------------------|----------|------|
| 1 | iCEasy (iceasy.com) | 200 | ✅ **可以** | HTTP + HTML解析 | 直接获取到阶梯价格 ¥0.07119 等 |
| 2 | 华秋商城 (hqchip.com) | 200 | ✅ **可以** | HTTP + HTML解析 | SSR 渲染,HTML含完整阶梯价格+库存 |
| 3 | 拍明芯城 (iczoom.com) | 200 | ⚠️ **部分可以** | HTTP + HTML解析 | HTML含品牌/型号列表,价格需详情页 |
| 4 | 唯样商城 (oneyac.com) | 200 | ❌ **不行** | 需要 Playwright 或逆向token | JSONP+客户端生成token,纯HTTP无法获取 |
| 5 | 万联芯城 (wlxmall.com) | 200 | ⚠️ **待确认** | 传统网站,可能可行 | 有产品数据但测试型号无结果,需国内IP验证 |
| 6 | 华强电子网 (hqew.com) | 200 | ⚠️ **部分可以** | HTTP + HTML解析 | s.hqew.com有搜索结果结构,但数据需JS加载 |
| 7 | 硬之城 (allchips.com) | 200 | ❌ **不行** | 需要 Playwright | 仅返回1KB验证页面,强反爬 |
| 8 | 猎芯网 (ichunt.com) | 重定向 | ❌ **不行** | 需要 Playwright | 重定向到v3/info,SPA架构 |
| 9 | IC交易网 (ic.net.cn) | 重定向 | ❌ **不行** | 需要登录+Playwright | 搜索强制跳转登录页 |
| 10 | ICGOO (icgoo.net) | 404 | ❌ **不行** | 需要 Playwright（或已下线） | SPA + 可能已关闭/改版 |
| 11 | 百能云芯 (icdeal.com) | WAF | ❌ **不行** | 需要 Playwright | WAF验证拦截 |
| 12 | 小猫芯城 (cmalls.net) | 200 | ❌ **不行** | 需要 Playwright | 699字节SPA骨架,数据全靠JS |
| 13 | 艾汐芯城 (ic-stk.cn) | 200 | ❌ **不行** | 需要 Playwright | 698字节反爬页面 (class="crawler") |
| 14 | 京东工品汇 (vipmro.com) | 493 | ❌ **不行** | 需要国内IP + Playwright | 地域封锁 (x-jfe-reason: deny:geo) |

---

## 详细验证结果

### ✅ 确认可用 HTTP 方案（3个）

#### 1. iCEasy (iceasy.com) — HTTP 直接可用
```
URL: https://www.iceasy.com/search?keyword=RC0402FR-0710KL
状态: 200
响应大小: 92,683 bytes
数据内容: 完整产品信息 + 阶梯价格
提取到的价格: ¥0.01921, ¥0.01808, ¥0.01695, ¥0.07119, ¥0.05311, ¥0.04972
```
**结论**: 完美的 HTTP + HTML 解析场景。搜索结果直接在 HTML 中渲染，含阶梯价格和库存。

#### 2. 华秋商城 (hqchip.com) — SSR 渲染
```
URL: https://www.hqchip.com/Search/index.html?keyword=RC0402FR-0710KL
状态: 200
响应大小: 108,651 bytes
SSR数据: 完整产品信息在 #resultTabBox 中
提取到: 型号=RC0402FR-0710KL, 品牌=Yageo, 封装=0402
阶梯价格: 100+: ¥0.00566, 500+: ¥0.00544, 1000+: ¥0.00511, 5000+: ¥0.00467, 10000+: ¥0.00441
库存: 2,000,000（询价）
```
**结论**: 虽然前端用 SeaJS 框架，但搜索结果是服务端渲染的（SSR），HTML 中直接包含完整产品数据。

**注意**: 华秋还有独立的搜索服务 `search.hqchip.com`，返回 JSON 但需要 token 认证。用 HTML 解析方式更简单可靠。

#### 3. 拍明芯城 (iczoom.com) — HTML 有产品列表
```
URL: https://www.iczoom.com/tourist/searchsell.html?param=RC0402FR-0710KL
状态: 200
响应大小: 527,768 bytes
数据: 含品牌YAGEO和型号RC0402FR-0710KL
```
**结论**: HTML 中有产品列表数据，但具体价格可能需要进一步解析或进入详情页。属于可行但需要额外工作的类型。

---

### ⚠️ 需要进一步验证（3个）

#### 4. 唯样商城 (oneyac.com) — JSONP + Token
```
实际 API: https://soic.oneyac.com/search?callback=jQuery...&paramsDTO={...}&token=on@hol11...eyac$der
认证方式: 客户端 JS 生成 token（含时间戳+加密）
数据格式: JSONP
浏览器验证: 页面正常展示产品数据（价格¥0.0091等）
```
**结论**: ❌ 纯 HTTP 不可行。API 需要客户端生成的 token，该 token 是 JS 运行时计算的。
**策略调整**: 
- 方案A: 逆向 JS 中的 token 生成算法（复杂但可行）
- 方案B: 使用 Playwright 执行页面 JS 后提取数据
- 方案C: 使用 Playwright 拦截 JSONP 响应

#### 5. 万联芯城 (wlxmall.com) — 传统服务端渲染
```
URL: https://www.wlxmall.com/search?keyword=STM32F103
状态: 200
响应大小: 221,905 bytes
```
**结论**: 页面是传统服务端渲染（非SPA），HTML 中有产品表格数据。测试型号 RC0402FR-0710KL 未找到结果（可能该站不售此型号），但 STM32F103 有结果。
**策略**: 大概率可以用 HTTP + HTML 解析，但需要从国内 IP 验证，且价格可能不在搜索结果页直接展示。

#### 6. 华强电子网 (hqew.com) — 部分服务端渲染
```
URL: https://s.hqew.com/?cid=0&q=RC0402FR-0710KL
状态: 200
数据: HTML 中有 "报价" "供应商" 等结构，但具体产品数据可能需 JS 加载
```
**结论**: 作为 B2B 报价聚合平台，搜索结果框架在 HTML 中，但实际报价数据可能通过 AJAX 加载。需要从国内 IP 进一步验证。

---

### ❌ 确认需要 Playwright（8个）

#### 7. 硬之城 (allchips.com)
```
响应: 仅 1,026 bytes
内容: 验证页面（反爬）
```
**原因**: 强反爬机制，检测到非浏览器环境直接返回验证页。

#### 8. 猎芯网 (ichunt.com)
```
所有搜索URL均重定向到 /v3/info
最终页面: 非搜索结果
```
**原因**: SPA 架构，搜索逻辑完全在客户端 JS 中执行。

#### 9. IC交易网 (ic.net.cn)
```
搜索URL: https://www.ic.net.cn/search.php?q=RC0402FR-0710KL
实际跳转: https://member.ic.net.cn/login.php?from=...
```
**原因**: 必须登录后才能搜索，无匿名搜索接口。需要维护登录 Session。

#### 10. ICGOO (icgoo.net)
```
状态: 重定向到 404 / err-info
浏览器访问: 同样 404
```
**原因**: 网站可能已改版、下线或有地域限制。需要确认是否仍在需求范围内。

#### 11. 百能云芯 (icdeal.com)
```
searchResult URL -> 跳转到 WAF 验证页面
WAF URL: https://waf.icdeal.com/waf/verification?source=...
```
**原因**: Web Application Firewall (WAF) 拦截非浏览器请求。

#### 12. 小猫芯城 (cmalls.net)
```
响应: 699 bytes
内容: SPA 骨架 HTML（无实际数据）
```
**原因**: 纯 SPA 架构，所有数据通过客户端 JS 加载。

#### 13. 艾汐芯城 (ic-stk.cn)
```
响应: 698 bytes
内容: 反爬页面 (class="crawler", id="crawler_img")
```
**原因**: 明确的爬虫检测机制，返回专门的反爬页面。

#### 14. 京东工品汇 (vipmro.com)
```
状态: 493
响应头: x-jfe-reason: deny:geo, x-jfe-action: forbidden
服务器: Jdcloud-FE
```
**原因**: 京东云 CDN 地域封锁，从海外 IP 无法访问。即使从国内访问，京东系反爬也很强。

---

## 修正后的技术策略分类

### 第一类: 官方 API（5个）—— 最优方案
| 网站 | 策略 | 难度 |
|------|------|------|
| Digi-Key | Product API V4 (OAuth2) | 中 |
| Mouser | Search API V2 (API Key) | 低 |
| element14 | Product Search API (API Key) | 低 |
| 立创商城 LCSC | 开放平台 API | 中 |
| 云汉芯城 | 官方数据对接 | 中 |

### 第二类: HTTP + HTML 解析（3个）—— 可行且稳定
| 网站 | 策略 | 难度 |
|------|------|------|
| iCEasy | HTTP GET + BeautifulSoup | 低 |
| 华秋商城 | HTTP GET + BeautifulSoup (SSR) | 低 |
| 拍明芯城 | HTTP GET + BeautifulSoup | 中 |

### 第三类: 需要 Playwright 浏览器（10个）—— 必须用浏览器
| 网站 | 原因 | 复杂度 |
|------|------|--------|
| 唯样商城 | JSONP + 客户端Token生成 | 高 |
| 华强电子网 | 数据 AJAX 加载 + 可能需国内IP | 中 |
| 万联芯城 | 待确认,可能需国内IP | 中 |
| 硬之城 | 强反爬验证 | 高 |
| 猎芯网 | SPA + 可能有滑块验证 | 高 |
| IC交易网 | 强制登录 | 中 |
| 百能云芯 | WAF拦截 | 高 |
| 小猫芯城 | 纯SPA | 中 |
| 艾汐芯城 | 反爬检测 | 高 |
| 京东工品汇 | 地域封锁 + 京东系反爬 | 极高 |

### 已失效/不可用（1个）
| 网站 | 状态 |
|------|------|
| ICGOO (icgoo.net) | 404/不可访问，可能已下线或改版 |

---

## 关键结论

### 原始预判 vs 实际验证

| 原始预判 | 实际结果 |
|----------|----------|
| 12个站点可用 HTTP | 仅 **3个** 确认可用 HTTP |
| 1个需要 Playwright | **10个** 需要 Playwright |
| 5个有 API | 5个有 API（未变） |

### 核心发现

1. **大部分国内电子元器件网站都有较强的反爬措施**，纯 HTTP 请求方案的适用范围远比预期小。

2. **SPA 架构普遍**: 小猫、ICGOO、猎芯等站点使用纯 SPA（Vue/React），数据完全通过 JS 异步加载。

3. **Token 保护**: 唯样商城虽然有明确的 API 接口，但使用客户端生成的加密 token 防止直接调用。

4. **地域限制**: 京东工品汇有明确的地域封锁，需要从国内 IP 访问。其他站点的反爬行为也可能与请求来源有关。

5. **WAF/反爬**: 硬之城、艾汐、百能云芯等使用专门的反爬/WAF 系统。

### 修正后的技术建议

```
实际可行的技术分层:
├── 第一层: 官方 API (5个) → 最稳定,最快
├── 第二层: HTTP + HTML 解析 (3个) → 稳定但需维护选择器
└── 第三层: Playwright 浏览器自动化 (10个) → 必须使用
    ├── 轻量级: 无需登录,只需JS执行 (华强、万联、小猫等)
    ├── 中等: 需要登录/Cookie管理 (IC交易网、唯样)
    └── 高难度: 反爬验证+登录 (硬之城、百能、猎芯)
```

### 对 Playwright 的优化建议

既然 10/18 个站点需要 Playwright，应该重点优化浏览器自动化方案：

1. **并发执行**: 多个浏览器实例并行处理不同站点
2. **Session 持久化**: 登录状态保存到文件，避免每次重新登录
3. **Stealth 插件**: playwright-stealth 必须使用
4. **智能等待**: 等待数据加载完成再提取，而非固定延时
5. **人工介入机制**: 验证码/滑块时暂停等待人工处理
6. **从国内部署**: 建议从国内服务器运行，避免地域限制问题

---

## 重要提醒

以上验证是从 **海外服务器** 发起的。从国内 IP 访问时，以下情况可能改善:
- 京东工品汇: 地域封锁解除
- 部分站点的反爬: 可能对国内 IP 更宽松
- 万联芯城/华强: 搜索功能可能正常返回数据

建议在正式开发时从 **国内环境** 重新验证一次。
