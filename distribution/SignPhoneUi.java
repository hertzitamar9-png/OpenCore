import java.io.File;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.cert.Certificate;
import java.util.Base64;
import java.util.HexFormat;

/**
 * Signs one release's phone interface with the Android app's own key. An
 * installed app accepts a live interface update only with this signature,
 * verified against the certificate that signed the app itself.
 *
 * Usage: java SignPhoneUi.java KEYSTORE ALIAS PASSWORD_ENV TAG REVISION
 * The password is read from the named environment variable, never argv.
 */
public class SignPhoneUi {
    public static void main(String[] args) throws Exception {
        if (args.length != 5) throw new IllegalArgumentException("usage: KEYSTORE ALIAS PASSWORD_ENV TAG REVISION");
        String password = System.getenv(args[2]), tag = args[3], revision = args[4];
        if (password == null || password.isEmpty()) throw new IllegalStateException("The Android signing password is missing");
        if (!tag.matches("app-v[0-9]{1,9}(\\.[0-9]{1,9}){1,3}") || !revision.matches("[a-f0-9]{64}"))
            throw new IllegalArgumentException("Invalid release tag or interface revision");
        char[] secret = password.toCharArray();
        KeyStore store = KeyStore.getInstance(new File(args[0]), secret);
        PrivateKey key = (PrivateKey) store.getKey(args[1], secret);
        Certificate certificate = store.getCertificate(args[1]);
        if (key == null || certificate == null) throw new IllegalStateException("The Android signing key is missing");
        String algorithm = switch (key.getAlgorithm()) {
            case "RSA" -> "SHA256withRSA";
            case "EC" -> "SHA256withECDSA";
            default -> throw new IllegalStateException("Unsupported Android signing key");
        };
        byte[] payload = ("OpenCore phone interface\n" + tag + "\n" + revision + "\n").getBytes(StandardCharsets.UTF_8);
        Signature signer = Signature.getInstance(algorithm);
        signer.initSign(key);
        signer.update(payload);
        byte[] signature = signer.sign();
        Signature check = Signature.getInstance(algorithm);
        check.initVerify(certificate.getPublicKey());
        check.update(payload);
        if (!check.verify(signature)) throw new IllegalStateException("The phone interface signature failed its self-check");
        String fingerprint = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded()));
        System.out.println("{\"schema\":1,\"tag\":\"" + tag + "\",\"revision\":\"" + revision + "\",\"algorithm\":\"" + algorithm
            + "\",\"certificateSha256\":\"" + fingerprint + "\",\"signature\":\"" + Base64.getEncoder().encodeToString(signature) + "\"}");
    }
}
