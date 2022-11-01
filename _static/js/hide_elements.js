// Hide submodules in diff
function hideSubmodules(){

    // Hide the entire submodule/subpackage sections
    if (document.querySelector("#subpackages")){
        document.querySelector("#subpackages").classList.add("hideElement");
    }
    if (document.querySelector("#submodules")){
        document.querySelector("#submodules").classList.add("hideElement");
    }
  
    // Hide the entire submodule/subpackage list items from toc
    if (document.querySelector('li p a[href="#subpackages"]')){
        document.querySelector('li p a[href="#subpackages"]').closest("li").classList.add("hideElement");
    }
    if (document.querySelector('li p a[href="#submodules"]')){
        document.querySelector('li p a[href="#submodules"]').closest("li").classList.add("hideElement");
    }

}
