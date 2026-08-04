# kotlinx.serialization: keep the generated serializers reachable from the
# @Serializable classes' companion objects.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class com.bb10d.remote.** {
    *** Companion;
}
-keepclasseswithmembers class com.bb10d.remote.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.bb10d.remote.**$$serializer { *; }

# OkHttp ships optional Conscrypt/BouncyCastle/OpenJSSE hooks.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
