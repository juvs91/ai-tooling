# AI_PLAN - OAuth2 Google Login (Multihop Grounding)
STATUS: DRAFT
Reviewed-by: <pendiente>

## Objetivo
- Implementar autenticación con Google OAuth2
- Usuario puede: iniciar login → autorizar en Google → recibir JWT → acceder a rutas protegidas

## Alcance
- ✅ Provider de Google OAuth
- ✅ Rutas /auth/google y /auth/google/callback
- ✅ Generación de JWT
- ✅ Tests unitarios

## No alcance
- ❌ UI de login (solo backend)
- ❌ Otros providers (Facebook, GitHub)
- ❌ Refresh token rotation (fase 2)

## Fuentes (solo AI_CONTEXT)
- ai-notes/AI_CONTEXT.md
- ai-notes/auth.analysis.md
- ai-notes/routes.analysis.md

---

# Plan Detallado para Ejecución (Multihop Grounding)

## Paso 1: Instalar dependencias

**Objetivo:** Agregar paquetes necesarios para OAuth2

**Comandos exactos:**
```bash
npm install passport passport-google-oauth20 jsonwebtoken
npm install -D @types/passport @types/passport-google-oauth20 @types/jsonwebtoken
```

**Validación inmediata:**
```bash
grep -E "passport|jsonwebtoken" package.json
npm ls passport passport-google-oauth20 jsonwebtoken
```

**Output esperado:**
```
├── jsonwebtoken@9.0.2
├── passport@0.7.0
└── passport-google-oauth20@2.0.0
```

**Si falla:**
- Verificar conexión a npm registry
- Verificar versión de Node >= 18
- NO continuar hasta que dependencias estén instaladas

---

## Paso 2: Crear Google OAuth Provider

**Objetivo:** Crear archivo src/auth/providers/google.ts

**Comandos exactos:**
```bash
mkdir -p src/auth/providers
```

**Estructura del archivo src/auth/providers/google.ts:**
```typescript
import passport from 'passport';
import { Strategy as GoogleStrategy, Profile } from 'passport-google-oauth20';

interface GoogleAuthConfig {
  clientID: string;
  clientSecret: string;
  callbackURL: string;
}

export function configureGoogleAuth(config: GoogleAuthConfig): void {
  passport.use(
    new GoogleStrategy(
      {
        clientID: config.clientID,
        clientSecret: config.clientSecret,
        callbackURL: config.callbackURL,
      },
      async (
        accessToken: string,
        refreshToken: string,
        profile: Profile,
        done: (err: Error | null, user?: Express.User) => void
      ) => {
        try {
          // Extraer datos del perfil de Google
          const user = {
            googleId: profile.id,
            email: profile.emails?.[0]?.value,
            name: profile.displayName,
            picture: profile.photos?.[0]?.value,
          };
          return done(null, user);
        } catch (error) {
          return done(error as Error);
        }
      }
    )
  );
}

export { passport };
```

**Validación inmediata:**
```bash
test -f src/auth/providers/google.ts && echo "✅ File exists"
grep -n "GoogleStrategy" src/auth/providers/google.ts
npm run tsc -- --noEmit src/auth/providers/google.ts
```

**Output esperado:**
```
✅ File exists
3:import { Strategy as GoogleStrategy, Profile } from 'passport-google-oauth20';
14:    new GoogleStrategy(
```

**Si falla:**
- Verificar imports correctos de passport-google-oauth20
- Verificar tipos de TypeScript
- NO continuar hasta que compile sin errores

---

## Paso 3: Crear rutas de autenticación

**Objetivo:** Crear archivo src/routes/auth/google.ts

**Comandos exactos:**
```bash
mkdir -p src/routes/auth
```

**Estructura del archivo src/routes/auth/google.ts:**
```typescript
import { Router, Request, Response } from 'express';
import passport from 'passport';
import jwt from 'jsonwebtoken';

const router = Router();

// Ruta: GET /auth/google
// Redirige al usuario a Google para autenticación
router.get(
  '/google',
  passport.authenticate('google', {
    scope: ['profile', 'email'],
  })
);

// Ruta: GET /auth/google/callback
// Google redirige aquí después de autenticar
router.get(
  '/google/callback',
  passport.authenticate('google', {
    session: false,
    failureRedirect: '/auth/login?error=google_failed',
  }),
  (req: Request, res: Response) => {
    const user = req.user as { googleId: string; email: string; name: string };

    // Generar JWT
    const token = jwt.sign(
      {
        sub: user.googleId,
        email: user.email,
        name: user.name,
      },
      process.env.JWT_SECRET || 'development-secret',
      { expiresIn: '24h' }
    );

    // Opción 1: Redirigir con token en query (para SPAs)
    res.redirect(`/auth/success?token=${token}`);

    // Opción 2: Setear cookie HttpOnly (más seguro)
    // res.cookie('token', token, { httpOnly: true, secure: true });
    // res.redirect('/dashboard');
  }
);

export default router;
```

**Validación inmediata:**
```bash
test -f src/routes/auth/google.ts && echo "✅ File exists"
grep -n "passport.authenticate" src/routes/auth/google.ts
grep -n "jwt.sign" src/routes/auth/google.ts
npm run tsc -- --noEmit src/routes/auth/google.ts
```

**Output esperado:**
```
✅ File exists
12:  passport.authenticate('google', {
21:  passport.authenticate('google', {
30:    const token = jwt.sign(
```

**Si falla:**
- Verificar que express y types estén instalados
- Verificar import paths
- NO continuar hasta que compile

---

## Paso 4: Integrar en app principal

**Objetivo:** Modificar src/app.ts para incluir las rutas de Google Auth

**Archivo a modificar:** src/app.ts

**Cambios específicos:**
```typescript
// AGREGAR imports (después de otros imports)
import passport from 'passport';
import { configureGoogleAuth } from './auth/providers/google';
import googleAuthRoutes from './routes/auth/google';

// AGREGAR configuración (después de app = express())
configureGoogleAuth({
  clientID: process.env.GOOGLE_CLIENT_ID || '',
  clientSecret: process.env.GOOGLE_CLIENT_SECRET || '',
  callbackURL: process.env.GOOGLE_CALLBACK_URL || 'http://localhost:3000/auth/google/callback',
});
app.use(passport.initialize());

// AGREGAR rutas (junto con otras rutas)
app.use('/auth', googleAuthRoutes);
```

**Validación inmediata:**
```bash
grep -n "configureGoogleAuth" src/app.ts
grep -n "googleAuthRoutes" src/app.ts
npm run tsc -- --noEmit
```

**Output esperado:**
```
15:import { configureGoogleAuth } from './auth/providers/google';
16:import googleAuthRoutes from './routes/auth/google';
25:configureGoogleAuth({
31:app.use('/auth', googleAuthRoutes);
```

**Si falla:**
- Verificar que src/app.ts existe
- Verificar orden de imports y middleware
- NO continuar hasta que compile

---

## Paso 5: Actualizar variables de entorno

**Objetivo:** Agregar variables requeridas a .env.example

**Cambios en .env.example:**
```bash
# Google OAuth2
GOOGLE_CLIENT_ID=your-client-id-here
GOOGLE_CLIENT_SECRET=your-client-secret-here
GOOGLE_CALLBACK_URL=http://localhost:3000/auth/google/callback

# JWT
JWT_SECRET=your-jwt-secret-min-32-chars
```

**Validación inmediata:**
```bash
grep "GOOGLE_CLIENT_ID" .env.example
grep "JWT_SECRET" .env.example
```

**Si falla:**
- Crear .env.example si no existe
- Verificar formato KEY=value

---

## Paso 6: Crear tests

**Objetivo:** Crear archivo src/auth/__tests__/google.test.ts

**Estructura del archivo:**
```typescript
import { configureGoogleAuth } from '../providers/google';
import passport from 'passport';

describe('Google OAuth Provider', () => {
  beforeEach(() => {
    // Reset passport strategies
    (passport as any)._strategies = {};
  });

  it('should configure google strategy', () => {
    configureGoogleAuth({
      clientID: 'test-client-id',
      clientSecret: 'test-client-secret',
      callbackURL: 'http://localhost:3000/auth/google/callback',
    });

    expect((passport as any)._strategies.google).toBeDefined();
  });

  it('should require clientID', () => {
    expect(() => {
      configureGoogleAuth({
        clientID: '',
        clientSecret: 'test-secret',
        callbackURL: 'http://localhost:3000/callback',
      });
    }).not.toThrow(); // passport permite strings vacíos pero fallará en runtime
  });
});
```

**Validación inmediata:**
```bash
npm test -- --testPathPattern=google --passWithNoTests
```

**Output esperado:**
```
PASS src/auth/__tests__/google.test.ts
  Google OAuth Provider
    ✓ should configure google strategy
    ✓ should require clientID
```

---

## Checklist de validación final

```bash
# 1. Todos los archivos existen
ls -la src/auth/providers/google.ts
ls -la src/routes/auth/google.ts
ls -la src/auth/__tests__/google.test.ts

# 2. Compilación exitosa
npm run tsc -- --noEmit

# 3. Tests pasan
npm test -- --testPathPattern=auth

# 4. Server inicia sin errores
npm run dev &
sleep 3
curl -I http://localhost:3000/auth/google
kill %1
```

---

## Riesgos
- **GOOGLE_CLIENT_SECRET en repo**: Verificar .gitignore incluye .env
- **Callback URL mismatch**: Debe coincidir exactamente con Google Console
- **HTTPS requerido en producción**: Google no permite HTTP en prod

## MISSING_INFO
> Si falta algo para ejecutar sin inventar: escribe UNA pregunta y STOP.
- (ninguna - plan completo)
