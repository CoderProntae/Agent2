# Agent2 — Yerel Yapay Zekâ Kodlama Ajanı

Agent2; **PySide6 ile oluşturulmuş yerel Windows masaüstü uygulamasıdır** (web uygulaması değildir). Seçtiğiniz çalışma klasörünü inceleyebilir, Ollama ile sohbet edebilir, onayınızla dosya değiştirip geliştirici komutları çalıştırabilir ve Git işlemlerini yürütebilir. Ayrı **UsageLimitEditor** uygulaması yerel kullanım kotalarını yönetir.

## Özellikler

- Koyu temalı çok bölmeli arayüz: çalışma alanı gezgini, sohbet/Markdown, eylem akışı, kod görünümü, yan yana fark paneli ve terminal çıktısı.
- Yerel Ollama `/api/tags` ve `/api/chat` istemcisi; NDJSON/SSE akışlarını okur, bağlantı hatalarında sınırlı yeniden dener ve varsayılan olarak `http://localhost:11435` kullanır.
- Varsayılan model: `qwen3.5-9b-abliterated`. Model adı ayarlardan değiştirilebilir; model Ollama'da ayrıca kurulu olmalıdır.
- Çalışma alanı sınırı, yol geçişi denetimi, sembolik bağlantıların reddi, atomik dosya yazımı ve dosya başına 1 MB sınırı.
- Dosya yazma/silme, terminal ve Git değişiklikleri **her eylemde görünür kullanıcı onayı** ister. Terminal kabuk olmadan çalışır, izin verilen araçlarla sınırlıdır ve zaman aşımı/çıktı sınırı uygular.
- İstek, girdi/çıktı token tahmini/ölçümü, yürütme ve etkin süre sayaçları SQLite üzerinde tutulur. Ollama gerçek token sayısını vermediğinde UTF-8 uzunluğundan yaklaşık hesaplanır.
- Kota yöneticisi: günlük istek/token/yürütme, oturum token/süresi ve komut zaman aşımı eşikleri; genel kilit, geliştirici geçersiz kılması, sayaç sıfırlama ve PBKDF2 ile saklanan yönetici parolası.
- Git durum/fark/geçmiş, init/add/commit/dal işlemleri ve isteğe bağlı HTTPS GitHub pull/push. GitHub tokenı düz metin ayar dosyasına değil işletim sistemi anahtarlığına yazılır.
- Push/PR/manuel Actions çalıştırmasında test ve Windows x64 paketlemesi; iki bağımsız `.exe` ortak ZIP artifact'inin içinde sunulur. `v*` etiketi push edildiğinde aynı ZIP taslak GitHub Release'e eklenir.

## Gereksinimler ve yerel çalıştırma

- Windows 10/11 x64 (paketlenmiş `.exe` hedefi), Python 3.11+ (kaynak koddan çalıştırma), Git ve Ollama.
- Python bağımlılıklarını kurup masaüstü uygulamasını başlatın:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Kota düzenleyicisini ayrı pencerede çalıştırmak için:

```powershell
python usage_limit_editor.py
```

İlk açılışta yönetici parolası belirleyin. Ana masaüstü uygulamasındaki **Kotalar** düğmesi bu aracı ayrı süreç olarak açar.

### Ollama'yı 11435 portunda başlatma

Agent2 özellikle `11435` portunu hedefler. Ollama'yı bu portla başlatın (mevcut Ollama servisi `11434` üzerinde çalışıyorsa önce onu durdurun):

```powershell
$env:OLLAMA_HOST = "127.0.0.1:11435"
ollama serve
```

Başka bir PowerShell penceresinde modelin kurulu olduğunu doğrulayın; model etiketi Ollama dağıtımınızda bulunmuyorsa uygun yerel etiketi Agent2 **Ayarlar** bölümünden seçin:

```powershell
$env:OLLAMA_HOST = "127.0.0.1:11435"
ollama list
ollama pull qwen3.5-9b-abliterated
```

Uygulama Ayarlar bölümünde Ollama adresini, model adını ve kurulu modelleri yenileme seçeneğini sunar. İsteklerin varsayılanı `http://localhost:11435` olarak kalır.

## Kullanım

1. Agent2'yi açıp **Klasör aç** ile yerel bir depo seçin.
2. Ayarlar'da Ollama bağlantısını ve kurulu modeli doğrulayın.
3. Görevinizi yazın; `Ctrl+Enter` veya **Gönder** ile başlayın.
4. Ajanın eylem kartlarını ve terminal çıktısını izleyin. Her yazma/silme/komut/Git değişikliğinde ayrıntıları inceleyip açıkça onaylayın veya reddedin.
5. Dosya ağacında bir dosyayı çift tıklayarak açın; kullanıcı düzenlemeleri **Kaydet** ile yazılır. Ajan ve kullanıcı değişiklikleri fark panelinde görüntülenir.
6. Kota eşiklerini, kilidi veya geliştirici geçersiz kılmasını ayrı **UsageLimitEditor** üzerinden düzenleyin.

### GitHub eşitlemesi

Ayarlar'da `https://github.com/sahip/depo` adresini ve isteğe bağlı dalı girin. Token **Güvenli kaydet** ile işletim sistemi anahtarlığına yazılır; kaynak kodu/JSON ayarlarına eklenmez. Ana penceredeki GitHub menüsünden **Pull** veya **Push** başlatılabilir. Pull yalnızca temiz çalışma ağacında fast-forward yapar. Push/pull işlemi öncesinde ayrıca onay alınır. GitHub'da gereken en düşük `Contents` yetkisine sahip token kullanın. Git commit için Git kullanıcı adı/e-postasının Git'te önceden yapılandırılmış olması gerekir.

## Test ve GitHub Actions ile `.exe` üretimi

Yerel testler:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

`.github/workflows/build.yml` Windows runner üzerinde bağımlılıkları kurar, testleri çalıştırır ve **Agent2.exe** ile **UsageLimitEditor.exe** dosyalarını PyInstaller ile bağımsız paketler. Workflow artifact bölümünden iki `.exe` dosyasını içeren `Agent2-Windows.zip` paketini indirebilirsiniz.

- Kod push'u ve pull request: test + paket artifact'i.
- Actions sekmesinden `Build Agent2 for Windows` → **Run workflow**: test + paket artifact'i.
- Taslak Release: `v0.1.0` gibi bir `v*` etiketi oluşturup push edin; workflow ZIP'i draft release'e ekler. PR veya sıradan dal push'unda otomatik Release açılmaz.

## Mimari

```text
agent2/
  app.py, admin_app.py       PySide6 uygulama girişleri
  core/
    agent.py                 Araç çağrısı döngüsü, token/kota denetimi
    ollama.py                HTTPX, keep-alive, yeniden deneme ve akış çözümleyici
    workspace.py             Kök dizin kapsamlı atomik dosya işlemleri ve diff kaydı
    terminal.py              Kabuksuz izin listeli süreç çalıştırıcı
    git_service.py            Yerel Git ve keyring tabanlı GitHub HTTPS eşitlemesi
    tools.py                  Ollama fonksiyon şemaları ve onay kapıları
    usage.py, sessions.py     SQLite kota/oturum kalıcılığı
    config.py, paths.py       Yerel tercih ve özel veri yolları
  ui/                         Ana masaüstü arayüzü, ayarlar ve QThread çalışanları
tests/                         Otomatik testler
.github/workflows/build.yml    Windows test/paketleme/release otomasyonu
```

## Güvenlik ve sınırlar

- Kodunuz ve model bağlamı seçtiğiniz yerel Ollama sunucusuna gönderilir. Yerel Ollama'yı ve model ağırlıklarını güvenilir kaynaktan edinin; gizli dosyaları çalışma alanına dahil etmeden önce dikkate alın.
- Çalışma alanı yolları kök dizine bağlanır; sembolik bağlantılar ve kök dışına çıkış reddedilir. Bu, dosya işlemi katmanında sınırlandırmadır; **OS düzeyinde sandbox değildir**.
- `python`, `npm`, `git` gibi onaylanan programlar kullanıcı hesabı yetkileriyle çalışır ve kendi başlarına dosya/ağ erişimi yapabilir. Yalnızca anladığınız komutları onaylayın. Kabuk zinciri ve yönlendirmeler bilerek desteklenmez.
- Kota veritabanı kullanıcı profiline özel dosya izinleri ve yönetici parola özeti ile korunur; bu, makine sahibine karşı değiştirilemez bir kurumsal lisans/DRM sistemi değildir. Geliştirici geçersiz kılması ve sayaç sıfırlama özellikle yönetici aracında bulunur.
- GitHub tokenı için işletim sistemi anahtarlığı gerekir. Anahtarlık kullanılamıyorsa token düz metin dosyaya düşürülmez; GitHub eşitlemesi devre dışı kalır.

## Sorun giderme

- Bağlantı hatası: Ollama'nın `127.0.0.1:11435` üzerinde çalıştığını, modelin `ollama list` çıktısında bulunduğunu ve Ayarlar'daki URL/modeli kontrol edin.
- Araç çağrısı yok: kullanılan modelin Ollama araç çağrılarını desteklediğini doğrulayın; desteklemiyorsa Ayarlar'dan başka bir model seçin. Qwen3.5/3.6/3.8 için Agent2 düşünme kanalını kapatır; boş son yanıt gelirse bir kez araçsız yanıt kurtarmayı dener.
- `XML syntax error` / `element <function> closed by </parameter>`: bu hata Ollama'nın model araç çağrısını ayrıştırırken oluşur. Agent2 güvenli biçimde bir kez yeniden dener; hata sürerse Ollama'yı güncelleyin veya araç çağrısı destekleyen başka model seçin. Daha önce başarıyla çalışan araç işlemleri geri alınmaz; tekrar denemeden önce dosya ağacını kontrol edin.
- Klasör/dosya oluşturma: `mkdir` gibi komutlar terminal izin listesinde değildir. Ajan dosyayı `write_file` ile çalışma alanına göreli yolda oluşturur ve gerekli üst klasörleri kendisi açar.
- GitHub erişim hatası: HTTPS depo adresini, token `Contents` izinlerini, Git kurulumunu ve temiz çalışma ağacını kontrol edin.
- Uygulama logları kullanıcı veri klasöründeki `Agent2/logs/agent2.log` dosyasına döner; sohbet/token içeriği loglara yazılmaz.
