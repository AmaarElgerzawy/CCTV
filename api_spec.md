# Dahua DVR Web API Spec (from sanitized HAR summary)

Base: `C:\Users\ammar2001\Downloads\har_summary.txt` (unique requests only: method/path, RPC method or CGI/action/name, status, field names; no values). Analysis is strictly from this file.

## 1) API style and distinct endpoints

**Style (confirmed):** Mix of JSON-RPC style calls over `/RPC2` and `/RPC2_Login`, plus some non-RPC endpoints (static/assets, init/checks, websocket upgrade path). 

Distinct endpoint paths observed (with counts):

| Path | Method(s) | Count | Notes |
|---|---|---|---|
| `/` | GET | 1 | Root (304) |
| `/current_config/preLanguage` | GET | 1 | Language pref (200) |
| `/custom_lang/English.txt` | GET | 1 | 404 |
| `/web_lang/English.txt` | GET | 2 | 200 then 304 |
| `/OutsideCmd` | POST (rpc=...) | 5 | Init/check calls before login (see below) |
| `/baseProj/defaultCap.json` | GET | 2 | 200 then 304; device capabilities/caps |
| `/RPC2_Login` | POST (rpc=global.login) | 1 | Login endpoint (confirmed) |
| `/RPC2` | POST (rpc=...) | 226 | Main JSON-RPC calls after login (majority) |
| `/RPC2_Loadfile/WebCapConfig` | POST | 1 | Loads web capability/config blob (keys shown, no rpc name visible) |
| `/app/plugin/platForm/h5/h5Video/Decode/ffmpegasm.js.mem` | GET | 1 | 404 |
| `/app/plugin/platForm/h5/h5Video/ffmpegasm.js.mem` (alternate path appears as 404 earlier, later 200) | GET | 1 (200) | Plugin asset (404 then 200 observed) |
| `/rtspoverwebsocket` | GET | 1 | HTTP upgrade to websocket (101 Switching Protocols) |
| `/` related assets as above | - | - | Static resources | 

**Confirmed:** JSON-RPC over `/RPC2_Login` and `/RPC2`. **Uncertain:** Whether all `/OutsideCmd` calls are considered "RPC" in same style - they use `rpc=` query/form-like param in the summary; structure similar but path differs from `/RPC2*`. Also `/RPC2_Loadfile/WebCapConfig` has no explicit `rpc=` method name visible in the summary.

## 2) Login and session flow (as visible)

Observed sequence (by order in file):

1. **GET /** -> 304
2. **GET /current_config/preLanguage** -> 200 (keys: `PreLanguage`)
3. **GET /custom_lang/English.txt** -> 404; **GET /web_lang/English.txt** -> 200
4. **POST /OutsideCmd rpc=Security.getEncryptInfo** -> 200 (keys: `params`, `result`, `params.AESPadding`, `params.asymmetric`, `params.cipher`, `params.pub`, `params.suggestedAsymmetric`) — *pre-login encryption/capability info*
5. **POST /OutsideCmd rpc=DevInit.getStatus** -> 200 (keys: `params`, `result`, `params.Find`, `params.Init`, `params.InitItems`, `params.PasswdFindVersion`, `params.SecretQuestion`, `params.Step`) — *init/status*
6. **GET /baseProj/defaultCap.json** -> 200 (device caps/flags)
7. **POST /OutsideCmd rpc=WebInit.getCaps** -> 200 (keys include `params.b_showDeviceInitNewPage`, `params.deviceClass`, `params.isPrivateDev`, `params.loginType`, `params.serialNo`, `params.supportDeviceInit`, `params.supportPasswordRetrieval`, `params.vendor`, etc.)
8. **POST /OutsideCmd rpc=Security.needLoginByUShield** -> 200 (keys: `error`, `result`) — *auth method hint*
9. **POST /RPC2_Login rpc=global.login** -> 200 (keys: `error`, `id`, `params`, `result`, `session`, `params.authorization`, `params.encryption`, `params.random`, `params.realm`) — **login**
10. **POST /RPC2 rpc=userManager.getPwdValidPeriod name=admin** -> 200 (keys: `id`, `params`, `result`, `session`, `params.RemainDays`, `params.TotalDays`) — post-login check
11. Many subsequent **POST /RPC2 rpc=...** calls include `session` in response keys (e.g. most RPC2 responses show `session`)

**Session/keepalive/logout (visible):**
- **Session**: After `global.login` on `/RPC2_Login`, subsequent `/RPC2` responses include `session` (field name only). The login request/response fields visible: `params.authorization`, `params.encryption`, `params.random`, `params.realm` (request/response structure fields as named). 
- **Keepalive**: **POST /RPC2 rpc=global.keepAlive** -> 200 (keys: `id`, `params`, `result`, `session`, `params.timeout`) — confirmed keepalive.
- **Logout**: **Not observed** in this HAR summary. No request with `logout`, `global.logout`, or similar method name appears. **Uncertain** - logout may exist but wasn't captured/sanitized into this summary.

**Field names involved (only as seen):** Pre-login: `params.AESPadding/asymmetric/cipher/pub/suggestedAsymmetric`, `params.Find/Init/InitItems/PasswdFindVersion/SecretQuestion/Step`, various caps flags. Login: `error`, `id`, `params`, `result`, `session`, `params.authorization/encryption/random/realm`. Post-login: `session` appears consistently; method-specific params/fields as listed per call.

**Note:** No credential values/cookies/session tokens are described (and none requested). Flow is documented structurally only.

## 3) Categorized requests (remaining)

| Category | Method/Path | RPC/Action | Purpose (inferred from method/name only) | Field names seen |
|---|---|---|---|---|
| **Device info** | POST /RPC2 | `magicBox.getDeviceType` | Device type | `id, params, result, session, params.type` |
|  | POST /RPC2 | `magicBox.getDeviceClass` | Device class | `id, params, result, session, params.type` |
|  | POST /RPC2 | `magicBox.getProductDefinition name=VideoAnalyse` | Product def query | `id, params, result, session, params.definition` |
|  | POST /RPC2 | `magicBox.getProductDefinition name=RemoteVideoAnalyse` | Product def query | `id, params, result, session, params.definition` |
|  | POST /RPC2 | `magicBox.getProductDefinition name=HasTalk` | Product def query | `id, params, result, session, params.definition` |
|  | POST /RPC2 | `magicBox.getProductDefinition name=BuildDate` | Product def query | `id, params, result, session, params.definition` |
|  | POST /RPC2 | `magicBox.getProductDefinition name=CVIVideoDecrypt` | Product def query | `id, params, result, session, params.definition` |
|  | POST /RPC2 | `magicBox.getSoftwareVersion` | Software version | `id, params, result, session, params.version` |
|  | POST /RPC2 | `magicBox.getSystemInfoNew` | System info | `error, id, params, result, session, params.info` |
|  | POST /RPC2 | `magicBox.get2DCode` | 2D code/info | `id, params, result, session, params.code` |
|  | POST /RPC2 | `global.getCurrentTime` | Current time/UTC | `id, params, result, session, params.time, params.utc` |
|  | POST /RPC2 | `IntervideoManager.getVersion` | Intervideo version | `id, params, result, session, params.info` |
|  | POST /RPC2 | `IntervideoClient.getVersion name=Onvif` | ONVIF client version | `id, params, result, session, params.info` |
| **Network** | POST /RPC2 | `netApp.getCaps` | Network app caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `NetworkSnifferManager.getSnifferInfo` | Sniffer info | `id, params, result, session, params.snifferInfos` |
| **Camera/Encode** | POST /RPC2 | `LogicDeviceManager.getVideoInputChannels` | Video input channels | `id, params, result, session, params.channelCountInfo` |
|  | POST /RPC2 | `LogicDeviceManager.getVideoChannelsInfo` | Video channels info | `id, params, result, session, params.Output` |
|  | POST /RPC2 | `LogicDeviceManager.getVideoOutputChannels` | Video output channels | `id, params, result, session, params.channelCountInfo` |
|  | POST /RPC2 | `devVideoInput.factory.instance` | Create video input instance | `id, result, session` |
|  | POST /RPC2 | `devVideoInput.getMaxSize` | Max size | `id, params, result, session, params.height, params.width` |
|  | POST /RPC2 | `devVideoInput.getCaps` | Video input caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `devVideoInput.getCapsEx` | Video input caps ex | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `encode.getConfigCaps` | Encode config caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `encode.getCaps` | Encode caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `encode.getSmartCaps` | Smart encode caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `encode.factory.instance` | Create encode instance | `id, result, session` |
|  | POST /RPC2 | `devVideoEncode.factory.instance` | Create video encode instance | `id, result, session` |
|  | POST /RPC2 | `devVideoEncode.getCaps` | Video encode caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `devAudioEncode.factory.instance` | Create audio encode instance | `id, result, session` |
|  | POST /RPC2 | `devAudioEncode.getCaps` | Audio encode caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `devAudioEncode.getFormatCaps` | Audio encode format caps | `id, params, result, session, params.formats` |
|  | POST /RPC2 | `configManager.getConfig name=Encode` | Read Encode config | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getDefault name=Encode` | Get default Encode config | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=VideoColor` | Read VideoColor | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=VideoInOptions` | Read VideoInOptions | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=VideoInPreviewOptions` | Read VideoInPreviewOptions | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=VideoInOptionsCollection` | Read VideoInOptionsCollection | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=ImageEnhancement` | Read ImageEnhancement | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=VideoInDenoise` | Read VideoInDenoise | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=VideoStandard` | Read VideoStandard | `id, params, result, session, params.table` |
|  | POST /RPC2 | `MediaManager.getCaps name=['EQ','Margin','MaxAnalogChannel','TwistedPair','VolumeDetect','AnomalyDetect','MutationDetect']` | Media caps (various) | `id, params, result, session, params.caps` |
| **Storage/Record** | POST /RPC2 | `storage.getDirectoryPaths` | Storage dirs | `id, params, result, session, params.list` |
|  | POST /RPC2 | `storage.getCaps` | Storage caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `storage.getPortInfo` | Storage port info | `id, params, result, session, params.info` |
|  | POST /RPC2 | `StorageDeviceManager.getDeviceInfos` | Storage device infos | `id, params, result, session, params.device` |
|  | POST /RPC2 | `workDirectory.factory.instance name=/` | Work dir instance root | `id, result, session` |
|  | POST /RPC2 | `workDirectory.factory.instance name=/dev/sda` | Work dir instance disk | `id, result, session` |
|  | POST /RPC2 | `workDirectory.getInfo` | Work dir info | `id, params, result, session, params.info` |
|  | POST /RPC2 | `workDirectory.getBitmapEx` | Bitmap/thumb data | `id, params, result, session, params.bitmap` |
|  | POST /RPC2 | `configManager.getConfig name=RecordMode` | Read RecordMode | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=Record` | Read Record config | `id, params, result, session, params.table` |
| **Events/Alarms** | POST /RPC2 | `alarm.getInSlots` | Alarm inputs | `id, params, result, session, params.inputs` |
|  | POST /RPC2 | `alarm.getOutSlots` | Alarm outputs | `id, params, result, session, params.outputs` |
|  | POST /RPC2 | `CoaxialAlarmManager.getCaps` | Coaxial alarm caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `eventManager.factory.instance` | Create event manager | `id, result, session` |
|  | POST /RPC2 | `eventManager.getCaps` | Event manager caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `configManager.getConfig name=AlarmOut` | Read AlarmOut | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=Alarm[0].EventHandler.MonitorTour[0].Split` | Read alarm event handler config | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=MotionDetect[0].EventHandler.MonitorTour[0].Split` | Read motion detect event handler config | `id, params, result, session, params.table` |
| **Users/System** | POST /RPC2_Login | `global.login` | Login | `error, id, params, result, session, params.authorization, params.encryption, params.random, params.realm` |
|  | POST /RPC2 | `userManager.getPwdValidPeriod name=admin` | Pwd valid period | `id, params, result, session, params.RemainDays, params.TotalDays` |
|  | POST /RPC2 | `userManager.getActiveUserInfoAll` | Active users | `id, params, result, session, params.users` |
|  | POST /RPC2 | `global.keepAlive` | Keepalive | `id, params, result, session, params.timeout` |
|  | POST /RPC2 | `global.getCurrentTime` | Time (also device info) | as above |
|  | POST /RPC2 | `system.multicall` | Multicall | `id, params, result, session` |
|  | POST /RPC2 | `system.multiSec` | Multi-sec | `id, params, result, session, params.content` |
|  | POST /RPC2 | `configManager.getConfig name=Locales` | Locales | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=WorkGroupMode` | WorkGroupMode | `id, params, result, session, params.table` |
|  | POST /RPC2 | `configManager.getConfig name=CloudUpgradeConfig` | Cloud upgrade config | `id, params, result, session, params.table` |
|  | POST /RPC2 | `CloudUpgrader.getAutoCheck` | Auto check | `id, params, result, session, params.flag` |
|  | POST /RPC2 | `CloudUpgrader.check` | Check update | `error, id, result, session` |
|  | POST /RPC2 | `upgrader.getChipList` | Chip list | `id, params, result, session, params.list` |
|  | POST /RPC2 | `ConfigRestore.getModuleList` | Config restore modules | `id, params, result, session, params.modules` |
|  | POST /RPC2 | `FileManager.list` | File manager list | `id, params, result, session, params.elementInfo` |
|  | POST /RPC2 | `Security.getEncryptInfo` (on /RPC2) | Encrypt info (post-login) | `id, params, result, session, params.AESPadding, params.asymmetric, params.cipher, params.pub, params.suggestedAsymmetric` |
|  | POST /RPC2 | `SecurityApp.getSecCap` | Security caps | `id, params, result, session, params.Caps` |
| **Playback/Search** | POST /RPC2 | `mediaFileFind.factory.create` | Create media file finder | `id, result, session` |
|  | POST /RPC2 | `mediaFileFind.findFile` | Find files | `error, id, result, session` |
|  | POST /RPC2 | `mediaFileFind.close` | Close finder | `id, result, session` |
|  | POST /RPC2 | `mediaFileFind.destroy` | Destroy finder | `id, result, session` |
|  | POST /RPC2 | `workDirectory.getBitmapEx` | Thumbnails (also storage) | `id, params, result, session, params.bitmap` |
| **PTZ** | POST /RPC2 | `ptz.factory.instance` | Create PTZ instance | `id, result, session` |
|  | POST /RPC2 | `ptz.getCurrentProtocolCaps` | PTZ protocol caps | `id, params, result, session, params.caps` |
|  | POST /RPC2 | `ptz.getProtocol` | PTZ protocol | `id, params, result, session, params.info` |
|  | POST /RPC2 | `configManager.getConfig name=Ptz` | Read PTZ config | `id, params, result, session, params.table` |
|  | POST /RPC2 | `commPort.getNumberOfPorts` | Comm ports count | `id, params, result, session, params.ports` |
|  | POST /RPC2 | `devComm.factory.instance` | Create comm instance | `id, result, session` |
|  | POST /RPC2 | `devComm.getCaps` | Comm caps | `id, params, result, session, params.caps` |
| **Other** | POST /RPC2 | `POS.getCaps`, `POS.getAll` | POS (errors seen) | caps: `error,id,result,session`; getAll: `error,id,result,session` |
|  | POST /RPC2 | `faceBoard.factory.instance`, `faceBoard.getCaps` | Face board | instance: `id,result,session`; caps: `id,params,result,session,params.caps` |
|  | POST /RPC2 | `LowRateWPAN.getCaps` | Low rate WPAN (error) | `error,id,result,session` |
|  | POST /RPC2 | `CoaxialPoC.getCaps` | Coaxial PoC | `id,params,result,session,params.caps` |
|  | POST /RPC2 | `CoaxialEQ.getCaps/getEQValue/getEQState` | Coaxial EQ | caps/state have fields as shown (getEQValue has error) |
|  | POST /RPC2 | `devVideoOutput.factory.instance/getCollect/getCaps/enumModes` | Video output | various fields as shown |
|  | POST /RPC2 | `split.factory.instance/getCaps` | Split/display | instance: `id,result,session`; caps: `id,params,result,session,params.caps` |
|  | POST /RPC2 | `LogicDeviceManager.getCameraState` | Camera state (error) | `error,id,result,session` |
|  | POST /RPC2 | `SwitchPoE.getInfo` | PoE info (error) | `error,id,result,session` |
|  | POST /RPC2 | `masterSlaveGroup.getCaps` | Master/slave (error) | `error,id,result,session` |
|  | POST /RPC2 | `DevVariableCapsManager.checkStart` | Variable caps (error) | `error,id,result,session` |
|  | POST /RPC2 | `webFunCollect.findItems` | Web function collect | `id,params,result,session,params.list` |
|  | POST /RPC2 | `speak.getCaps` | Speak caps | `id,params,result,session,params.caps` |
|  | POST /RPC2 | `commPort.getDeviceCount` | Comm device count | `id,params,result,session,params.count` |
|  | POST /RPC2_Loadfile/WebCapConfig | (no rpc visible) | Web cap config blob | `DockUser, IntelliVersion, SetReduceNoiseMode, SupportImportExportChnInfo, SupportNetAlarmCenter, SupportNetSNMP, a_connectRange, a_ftpRecordType, a_invalidPage, a_lockTimeLimit, a_qrCode, a_talkTypes, a_tryLoginLimit, acutanceGroup, bShowAlarmCenterDoubleIp, b_3GKeepAlive, b_AudioSingle, b_BitrateLevel, b_FaceConfigOneChn, b_SplitPreview, b_adjustTimeFormat, b_changeDiskTypeReboot, b_chnlPreview, b_compelPassword, b_diskGroupReboot` |
|  | GET/WS | `/rtspoverwebsocket` (101) | WebSocket upgrade for RTSP-over-websocket (plugin/live path) | - |
|  | GET | Plugin assets (`ffmpegasm.js.mem`) | H5 video plugin assets | - |

## 4) Config areas: read/write via configManager/RPC

From file, reads are done via `configManager.getConfig name=<Name>` and `configManager.getDefault name=<Name>`. **Writes/set operations not observed** in this summary - no `configManager.setConfig`, `setConfig` with params, or write-style calls appear. All config-related calls are gets/defaults.

| Area | Read (RPC/method + name/params) | Write (as seen) | Field names seen (read) |
|---|---|---|---|
| **Device/System** | `configManager.getConfig name=Locales` | **Uncertain** (not observed) | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=WorkGroupMode` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=VideoStandard` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=CloudUpgradeConfig` | **Uncertain** | `id, params, result, session, params.table` |
| **Network** | **Uncertain** - no explicit `configManager.getConfig name=Network`/`Net` seen. `netApp.getCaps`, `NetworkSnifferManager.getSnifferInfo` seen as RPCs (caps/info), not named config gets. | **Uncertain** | - |
| **Camera/Encode** | `configManager.getConfig name=Encode` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getDefault name=Encode` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=VideoColor` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=VideoInOptions` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=VideoInPreviewOptions` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=VideoInOptionsCollection` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=ImageEnhancement` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=VideoInDenoise` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=MediaEncrypt` | **Uncertain** | `id, params, result, session, params.table` |
| **Storage/Record** | `configManager.getConfig name=RecordMode` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=Record` | **Uncertain** | `id, params, result, session, params.table` |
| **Events/Alarms** | `configManager.getConfig name=AlarmOut` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=Alarm[0].EventHandler.MonitorTour[0].Split` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=MotionDetect[0].EventHandler.MonitorTour[0].Split` | **Uncertain** | `id, params, result, session, params.table` |
| **PTZ** | `configManager.getConfig name=Ptz` | **Uncertain** | `id, params, result, session, params.table` |
| **Video Out/Display** | `configManager.getConfig name=VideoOut` | **Uncertain** | `id, params, result, session, params.table` |
|  | `configManager.getConfig name=MonitorTour` | **Uncertain** | `id, params, result, session, params.table` |
| **Other** | `configManager.getConfig name=ExtendChannels` | **Uncertain** | `id, params, result, session, params.table` |

**Confirmed pattern:** Reads use `configManager.getConfig` with `name=` (and sometimes indexed names like `Alarm[0]...`, `MotionDetect[0]...`). **Write operations (setConfig/save) are not present** in this sanitized summary - cannot confirm method names/field names for writes from this file alone.

## 5) Plugins/WebSocket/proprietary protocols (live video)

**Confirmed:**
- **WebSocket upgrade:** `GET /rtspoverwebsocket` -> **101 Switching Protocols** (line 107). This indicates RTSP-over-WebSocket (common for browser live video).
- **H5 video plugin assets:** `GET /app/plugin/platForm/h5/h5Video/ffmpegasm.js.mem` (404 then later 200 observed) - suggests browser-side decoding/plugin components (FFmpeg asm.js/WASM mem file). 
- **FFmpeg/WASM presence:** plugin path references `h5Video/Decode/ffmpegasm.js.mem` and `h5Video/ffmpegasm.js.mem`.

**Uncertain/flagged as requiring separate handling:**
- **Live video streaming:** The web UI likely uses RTSP-over-WebSocket (`/rtspoverwebsocket`) or plugin-based streaming; the summary does not show raw RTSP URLs/ports or media payloads (sanitized). Since you plan to handle video separately via RTSP, this matches - live video appears plugin/WebSocket related rather than simple JSON API calls returning frames.
- **Proprietary protocols:** Many RPCs go through Dahua's JSON-RPC style (`/RPC2*`) which is proprietary API surface; P2P/other mechanisms not visible in HTTP HAR summary. 

**Recommendation:** Treat live preview/playback streaming as separate from REST/RPC control API - use RTSP directly as planned.

## 6) Minimal prioritized list for Python backend (first version)

Based strictly on what's confirmed in the file:

| Priority | Call | Endpoint/Method | Purpose | Notes |
|---|---|---|---|---|
| **1** | Pre-login encryption/caps (if needed) | `POST /OutsideCmd rpc=Security.getEncryptInfo` | Get encryption params before login | Confirmed present; field names seen. May be required depending on auth flow. |
| **2** | Login | `POST /RPC2_Login rpc=global.login` | Authenticate | Confirmed. Needs login params per device flow (fields: `params.authorization/encryption/random/realm` visible as structure). |
| **3** | Post-login session check | `POST /RPC2 rpc=userManager.getPwdValidPeriod name=admin` | Verify session | Confirmed; uses `session`. |
| **4** | Keepalive | `POST /RPC2 rpc=global.keepAlive` | Maintain session | Confirmed (returns `params.timeout`). |
| **5** | Device info | `POST /RPC2 rpc=magicBox.getDeviceType`, `magicBox.getDeviceClass`, `magicBox.getSoftwareVersion`, `global.getCurrentTime` | Basic device identity/time | Multiple confirmed calls. |
| **6** | List channels | `POST /RPC2 rpc=LogicDeviceManager.getVideoInputChannels` | Channel list/count | Confirmed. |
| **7** | Read Network config | `POST /RPC2 rpc=netApp.getCaps` (+ `NetworkSnifferManager.getSnifferInfo` if needed) | Network capabilities/info | **Uncertain** if `configManager.getConfig name=Network*` exists - not seen. Use `netApp.getCaps` as confirmed network-related call; full network config read method not visible in summary. |
| **8** | Write Network config | **Uncertain** | Set network settings | **Not observed** in summary - no set/write method names/field names visible. Cannot specify from this file. |
| **9** | Read Encode config | `POST /RPC2 rpc=configManager.getConfig name=Encode` (+ `encode.getCaps`, `encode.getConfigCaps`) | Read encode settings | Confirmed read path via configManager. |
| **10** | Logout | **Uncertain** | End session | **Not observed** in summary. |

**Priority order for first version:** 1) Login flow (with encryption if required) → 2) keepAlive mechanism understanding → 3) device info + channels → 4) read Encode config → 5) network (read caps/info now, write later once discovered) → 6) logout if found/tested.

## Summary

**Confirmed:**
- API is **JSON-RPC over `/RPC2_Login` (global.login) and `/RPC2`** with session-based calls; also uses `/OutsideCmd` for pre-login init/encryption/caps.
- Login flow is visible: pre-login checks (`Security.getEncryptInfo`, `DevInit.getStatus`, `WebInit.getCaps`, `Security.needLoginByUShield`) → `POST /RPC2_Login rpc=global.login` (fields include `params.authorization/encryption/random/realm`, response includes `session`) → post-login calls include `session`.
- Keepalive confirmed: `global.keepAlive` returns `params.timeout`. 
- Config reads use `configManager.getConfig name=...` (and `getDefault`); many areas covered (Encode, Record, PTZ, Alarms, VideoIn/Color, etc.). 
- Live video likely via **WebSocket upgrade `/rtspoverwebsocket` (101)** + H5 plugin assets - handle separately via RTSP as planned.

**Uncertain:**
- **Network config write/read full config**: `netApp.getCaps`/sniffer info seen, but no `configManager.getConfig name=Network` or write method observed.
- **Config writes in general**: No `configManager.setConfig`/write calls visible in summary - only gets/defaults. Method names/field names for writes unknown.
- **Logout**: Not observed - method/endpoint unknown.
- **Full login param construction**: Field names visible (`authorization/encryption/random/realm`) but values/encoding not in summary (and not needed). Exact construction details uncertain from sanitized data.
- **Some RPCs return errors** (POS, LowRateWPAN, CoaxialEQ.getEQValue, SwitchPoE, etc.) - behavior uncertain.

**What to test next with a single request:**
1. **Test keepalive pattern first (safest post-login):** After a successful login in your environment, call `POST /RPC2` with `{"method":"global.keepAlive", ...}` and observe response structure (confirm `session` handling and `params.timeout`). This validates session mechanics with minimal risk.
2. **If network write needed:** Try `configManager.getConfig name=Network` (or common variants like `Network`, `TCPIP`, `Ethernet`) as a single read test to see if it exists; only attempt writes if you find the read config and understand its structure. No write tested here - confirm read existence first. 

*All findings are strictly from `har_summary.txt`. No values, credentials, cookies, or tokens are included.*
