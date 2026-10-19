<#import "footer.ftl" as loginFooter>
<#--
  MATE layout: every Keycloak page renders inside the platform's login card
  (apps/web/app/(auth)/login/page.tsx).

  Sections, scripts and the form contract are Keycloak's stock template.ftl
  (26.0) unchanged; only the markup around them differs. When upgrading
  Keycloak, diff this file against the stock one (see ../../README.md).
-->
<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html<#if realm.internationalizationEnabled> lang="${locale.currentLanguageTag}" dir="${(locale.rtl)?then('rtl','ltr')}"<#else> lang="en"</#if>>

<head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="robots" content="noindex, nofollow">

    <#if properties.meta?has_content>
        <#list properties.meta?split(' ') as meta>
            <meta name="${meta?split('==')[0]}" content="${meta?split('==')[1]}"/>
        </#list>
    </#if>
    <title>${msg("loginTitle",(realm.displayName!''))}</title>
    <link rel="icon" type="image/svg+xml" href="${url.resourcesPath}/img/favicon.svg" />
    <#if properties.stylesCommon?has_content>
        <#list properties.stylesCommon?split(' ') as style>
            <link href="${url.resourcesCommonPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if properties.scripts?has_content>
        <#list properties.scripts?split(' ') as script>
            <script src="${url.resourcesPath}/${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="importmap">
        {
            "imports": {
                "rfc4648": "${url.resourcesCommonPath}/vendor/rfc4648/rfc4648.js"
            }
        }
    </script>
    <script src="${url.resourcesPath}/js/menu-button-links.js" type="module"></script>
    <script src="${url.resourcesPath}/js/mate.js" defer></script>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="module">
        import { startSessionPolling } from "${url.resourcesPath}/js/authChecker.js";

        startSessionPolling(
          "${url.ssoLoginInOtherTabsUrl?no_esc}"
        );
    </script>
</head>

<#local brand = "MATE">
<#if realm.displayName?has_content><#local brand = realm.displayName></#if>

<body>
<div class="mate-page">
    <div class="mate-aurora" aria-hidden="true">
        <div class="mate-aurora-layer"><i></i><i></i><i></i></div>
    </div>

    <main class="mate-card">
        <span class="mate-beam" aria-hidden="true"></span>

        <#if realm.internationalizationEnabled  && locale.supported?size gt 1>
            <div class="${properties.kcLocaleMainClass!}" id="kc-locale">
                <div id="kc-locale-wrapper">
                    <div id="kc-locale-dropdown" class="menu-button-links">
                        <button tabindex="1" id="kc-current-locale-link" aria-label="${msg("languages")}" aria-haspopup="true" aria-expanded="false" aria-controls="language-switch1">${locale.current}</button>
                        <ul role="menu" tabindex="-1" aria-labelledby="kc-current-locale-link" aria-activedescendant="" id="language-switch1" class="${properties.kcLocaleListClass!}">
                            <#assign i = 1>
                            <#list locale.supported as l>
                                <li role="none">
                                    <a role="menuitem" id="language-${i}" class="${properties.kcLocaleItemClass!}" href="${l.url}">${l.label}</a>
                                </li>
                                <#assign i++>
                            </#list>
                        </ul>
                    </div>
                </div>
            </div>
        </#if>

        <#-- Both icons are always in the markup; css/mate.css shows the one for the current theme. -->
        <button type="button" id="mate-theme-toggle" class="mate-icon-btn mate-theme-toggle"
                aria-label="${msg("mateThemeToDark")}"
                data-label-light="${msg("mateThemeToLight")}" data-label-dark="${msg("mateThemeToDark")}">
            <svg class="mate-theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>
            <svg class="mate-theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
        </button>

        <header class="mate-card-header">
            <#-- The Mate mark: apps/web/components/mate-logo.tsx. -->
            <svg class="mate-logo" viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
                <rect x="8" y="8" width="20" height="20" rx="5.5" stroke="currentColor" stroke-width="2.5"/>
                <rect x="36" y="8" width="20" height="20" rx="5.5" stroke="currentColor" stroke-width="2.5"/>
                <rect x="36" y="36" width="20" height="20" rx="5.5" fill="currentColor"/>
                <rect x="8" y="36" width="20" height="20" rx="5.5" stroke="currentColor" stroke-width="2.5"/>
            </svg>
            <p class="mate-brand">${brand}</p>
            <h1 id="kc-page-title" class="mate-title"><#nested "header"></h1>
            <#if displayRequiredFields>
                <p class="mate-required"><span class="required">*</span> ${msg("requiredFields")}</p>
            </#if>
            <#if auth?has_content && auth.showUsername() && !auth.showResetCredentials()>
                <#nested "show-username">
                <div id="kc-username" class="mate-username">
                    <span id="kc-attempted-username">${auth.attemptedUsername}</span>
                    <a id="reset-login" class="mate-icon-btn" href="${url.loginRestartFlowUrl}" aria-label="${msg("restartLoginTooltip")}" title="${msg("restartLoginTooltip")}">
                        <i class="${properties.kcResetFlowIcon!}" aria-hidden="true"></i>
                    </a>
                </div>
            </#if>
        </header>

        <div id="kc-content">
            <div id="kc-content-wrapper">

                <#-- App-initiated actions should not see warning messages about the need to complete the action -->
                <#-- during login.                                                                               -->
                <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
                    <div class="${properties.kcAlertClass!} alert-${message.type}" role="<#if message.type = 'error'>alert<#else>status</#if>">
                        ${kcSanitize(message.summary)?no_esc}
                    </div>
                </#if>

                <#nested "form">

                <#if auth?has_content && auth.showTryAnotherWayLink()>
                    <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post">
                        <div class="${properties.kcFormGroupClass!}">
                            <input type="hidden" name="tryAnotherWay" value="on"/>
                            <a href="#" id="try-another-way"
                               onclick="document.forms['kc-select-try-another-way-form'].requestSubmit();return false;">${msg("doTryAnotherWay")}</a>
                        </div>
                    </form>
                </#if>

                <#nested "socialProviders">

                <#if displayInfo>
                    <div id="kc-info">
                        <div id="kc-info-wrapper">
                            <#nested "info">
                        </div>
                    </div>
                </#if>
            </div>
        </div>

        <@loginFooter.content/>
    </main>
</div>
</body>
</html>
</#macro>
