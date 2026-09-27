package bot.mesa;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WebView web = new WebView(this);
        setContentView(web);

        WebSettings ajustes = web.getSettings();
        ajustes.setJavaScriptEnabled(true);
        ajustes.setDomStorageEnabled(true);

        web.addJavascriptInterface(new Puente(), "Mesa");

        WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return loader.shouldInterceptRequest(request.getUrl());
            }
        });
        web.loadUrl("https://appassets.androidplatform.net/assets/index.html");
    }

    public static final class Puente {
        @JavascriptInterface
        public String pedir(String url) throws Exception {
            HttpURLConnection con = (HttpURLConnection) new URL(url).openConnection();
            con.setConnectTimeout(15000);
            con.setReadTimeout(30000);
            con.setInstanceFollowRedirects(true);
            con.setRequestProperty("Accept", "application/json");
            int status;
            try {
                status = con.getResponseCode();
            } catch (Exception e) {
                con.disconnect();
                throw e;
            }
            if (status < 200 || status >= 300) {
                con.disconnect();
                throw new Exception("HTTP " + status);
            }
            StringBuilder cuerpo = new StringBuilder();
            try (BufferedReader lector = new BufferedReader(new InputStreamReader(
                    (InputStream) con.getInputStream(), StandardCharsets.UTF_8))) {
                char[] buf = new char[8192];
                int n;
                while ((n = lector.read(buf)) >= 0) {
                    cuerpo.append(buf, 0, n);
                }
            } finally {
                con.disconnect();
            }
            return cuerpo.toString();
        }
    }
}
